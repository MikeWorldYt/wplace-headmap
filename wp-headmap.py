#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import math
from collections import defaultdict
from io import BytesIO

import requests
from PIL import Image

# ==========================
# CONFIGURACIÓN INICIAL
# ==========================

# Parámetros del rectángulo (ejemplo que diste)
START = {
    "tlx": 622,
    "tly": 1225,
    "pxx": 74,
    "pxy": 977,
}

END = {
    "tlx": 622,
    "tly": 1226,
    "pxx": 601,
    "pxy": 138,
}

# Endpoints base
BASE_PIXEL_URL = "https://backend.wplace.live/s0/pixel/{tlx}/{tly}?x={px}&y={py}"
BASE_TILE_URL = "https://backend.wplace.live/files/s0/tiles/{tlx}/{tly}.png"

# Carpeta de salida
OUTPUT_DIR = "output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

RECT_IMAGE_PATH = os.path.join(OUTPUT_DIR, "rect.png")
DATA_JSON_PATH = os.path.join(OUTPUT_DIR, "data.json")
HTML_PATH = os.path.join(OUTPUT_DIR, "index.html")


# ==========================
# UTILIDADES DE COORDENADAS
# ==========================

def to_world_coords(tlx, tly, pxx, pxy):
    """
    Convierte coordenadas (tile + pixel) a coordenadas absolutas (world).
    Cada tile es de 1000x1000 píxeles.
    """
    wx = tlx * 1000 + pxx
    wy = tly * 1000 + pxy
    return wx, wy


def rect_world_bounds(start, end):
    """
    Devuelve (wx0, wy0, wx1, wy1) en coordenadas absolutas del rectángulo.
    wx1/wy1 son inclusivos.
    """
    wx0, wy0 = to_world_coords(start["tlx"], start["tly"], start["pxx"], start["pxy"])
    wx1, wy1 = to_world_coords(end["tlx"], end["tly"], end["pxx"], end["pxy"])
    if wx1 < wx0 or wy1 < wy0:
        raise ValueError("Rectángulo inválido: el final está antes del inicio.")
    return wx0, wy0, wx1, wy1


def pixel_iterator(start, end):
    """
    Itera píxel a píxel desde start hasta end (incluyendo ambos),
    en orden de world_y (fila) y luego world_x (columna).
    """
    wx0, wy0, wx1, wy1 = rect_world_bounds(start, end)

    for wy in range(wy0, wy1 + 1):
        tly = wy // 1000
        pxy = wy % 1000
        for wx in range(wx0, wx1 + 1):
            tlx = wx // 1000
            pxx = wx % 1000
            yield tlx, tly, pxx, pxy


# ==========================
# BACKEND: FETCH PIXEL Y TILES
# ==========================

def fetch_pixel_info(tlx, tly, pxx, pxy):
    url = BASE_PIXEL_URL.format(tlx=tlx, tly=tly, px=pxx, py=pxy)
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return r.json()


def fetch_tile_image(tlx, tly):
    url = BASE_TILE_URL.format(tlx=tlx, tly=tly)
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    img = Image.open(BytesIO(r.content)).convert("RGBA")
    return img


# ==========================
# CONSTRUCCIÓN DE LA IMAGEN DEL RECTÁNGULO
# ==========================

def build_rect_image(start, end, save_path):
    """
    Descarga los tiles necesarios y construye la imagen del rectángulo.
    """
    wx0, wy0, wx1, wy1 = rect_world_bounds(start, end)
    width = wx1 - wx0 + 1
    height = wy1 - wy0 + 1

    # Determinar rango de tiles que toca el rectángulo
    tlx_min = wx0 // 1000
    tlx_max = wx1 // 1000
    tly_min = wy0 // 1000
    tly_max = wy1 // 1000

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    for tly in range(tly_min, tly_max + 1):
        for tlx in range(tlx_min, tlx_max + 1):
            try:
                tile_img = fetch_tile_image(tlx, tly)
            except Exception as e:
                print(f"[WARN] No se pudo descargar tile ({tlx}, {tly}): {e}")
                continue

            # world coords del tile completo
            tile_wx0 = tlx * 1000
            tile_wy0 = tly * 1000
            tile_wx1 = tile_wx0 + 999
            tile_wy1 = tile_wy0 + 999

            # intersección con el rectángulo
            inter_wx0 = max(wx0, tile_wx0)
            inter_wy0 = max(wy0, tile_wy0)
            inter_wx1 = min(wx1, tile_wx1)
            inter_wy1 = min(wy1, tile_wy1)

            if inter_wx0 > inter_wx1 or inter_wy0 > inter_wy1:
                continue  # no hay intersección

            # recorte dentro del tile
            crop_x0 = inter_wx0 - tile_wx0
            crop_y0 = inter_wy0 - tile_wy0
            crop_x1 = inter_wx1 - tile_wx0 + 1
            crop_y1 = inter_wy1 - tile_wy0 + 1

            tile_crop = tile_img.crop((crop_x0, crop_y0, crop_x1, crop_y1))

            # posición en el canvas
            paste_x = inter_wx0 - wx0
            paste_y = inter_wy0 - wy0

            canvas.paste(tile_crop, (paste_x, paste_y))

    canvas.save(save_path)
    print(f"[OK] Imagen del rectángulo guardada en: {save_path}")


# ==========================
# RECOLECCIÓN DE DATOS DE PINTURA
# ==========================

def collect_paint_data(start, end):
    """
    Recorre todos los píxeles del rectángulo, consulta el backend
    y construye:
      - conteo de píxeles por pintor
      - lista de pintores por píxel
    """
    painter_counts = defaultdict(int)
    pixel_painters = defaultdict(set)

    total = 0
    wx0, wy0, wx1, wy1 = rect_world_bounds(start, end)
    total_pixels = (wx1 - wx0 + 1) * (wy1 - wy0 + 1)
    print(f"[INFO] Total de píxeles a procesar: {total_pixels}")

    for tlx, tly, pxx, pxy in pixel_iterator(start, end):
        total += 1
        if total % 1000 == 0:
            print(f"[INFO] Procesados {total}/{total_pixels} píxeles...")

        try:
            data = fetch_pixel_info(tlx, tly, pxx, pxy)
        except Exception as e:
            print(f"[WARN] Error al obtener pixel ({tlx},{tly},{pxx},{pxy}): {e}")
            continue

        painted = data.get("paintedBy") or {}
        name = painted.get("name")
        pid = painted.get("id")

        if name is None or pid is None:
            continue

        key = f"{name}#{pid}"
        painter_counts[key] += 1

        wx, wy = to_world_coords(tlx, tly, pxx, pxy)
        rel_x = wx - wx0
        rel_y = wy - wy0
        pixel_painters[(rel_x, rel_y)].add(key)

    print(f"[OK] Recolección de datos completada. Píxeles procesados: {total}")
    return painter_counts, pixel_painters


# ==========================
# EXPORTAR JSON + HTML
# ==========================

def export_data_json(start, end, painter_counts, pixel_painters, path):
    wx0, wy0, wx1, wy1 = rect_world_bounds(start, end)

    data = {
        "rect": {
            "start": start,
            "end": end,
            "world": {
                "wx0": wx0,
                "wy0": wy0,
                "wx1": wx1,
                "wy1": wy1,
            },
            "width": wx1 - wx0 + 1,
            "height": wy1 - wy0 + 1,
        },
        "painterCounts": [
            {"key": key, "count": count}
            for key, count in sorted(painter_counts.items(), key=lambda x: x[1], reverse=True)
        ],
        "pixels": [
            {
                "x": x,
                "y": y,
                "painters": list(painters),
            }
            for (x, y), painters in pixel_painters.items()
        ],
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"[OK] JSON de datos guardado en: {path}")


def export_html(path):
    """
    HTML simple que carga rect.png y data.json, y permite seleccionar pintores
    para ver dónde pintaron (heatmap).
    """
    html = r"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <title>Mapa de calor - wplace</title>
  <style>
    body {
      font-family: sans-serif;
      display: flex;
      gap: 20px;
    }
    #container {
      position: relative;
      display: inline-block;
    }
    #base {
      display: block;
      image-rendering: pixelated;
    }
    #overlay {
      position: absolute;
      top: 0;
      left: 0;
      pointer-events: none;
      image-rendering: pixelated;
    }
    #painter-list {
      max-height: 90vh;
      overflow-y: auto;
      padding: 0;
      margin: 0;
      list-style: none;
      font-size: 14px;
    }
    .painter-item {
      cursor: pointer;
      padding: 2px 4px;
    }
    .painter-item.selected {
      background: #007acc;
      color: white;
    }
  </style>
</head>
<body>
  <div id="container">
    <img id="base" src="rect.png" alt="Rectángulo">
    <canvas id="overlay"></canvas>
  </div>

  <ul id="painter-list"></ul>

  <script>
    let data = null;
    let selected = new Set();
    let ctx = null;
    let canvas = null;

    fetch('data.json')
      .then(r => r.json())
      .then(d => {
        data = d;
        init();
      })
      .catch(err => {
        console.error('Error cargando data.json', err);
      });

    function init() {
      const img = document.getElementById('base');
      canvas = document.getElementById('overlay');
      ctx = canvas.getContext('2d');

      img.onload = () => {
        canvas.width = img.width;
        canvas.height = img.height;
        renderPainterList();
        drawOverlay();
      };
    }

    function renderPainterList() {
      const ul = document.getElementById('painter-list');
      ul.innerHTML = '';

      data.painterCounts.forEach(p => {
        const li = document.createElement('li');
        li.textContent = `${p.key} (${p.count})`;
        li.className = 'painter-item';
        li.onclick = () => {
          if (selected.has(p.key)) {
            selected.delete(p.key);
            li.classList.remove('selected');
          } else {
            selected.add(p.key);
            li.classList.add('selected');
          }
          drawOverlay();
        };
        ul.appendChild(li);
      });
    }

    function drawOverlay() {
      if (!ctx || !data) return;
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      if (selected.size === 0) {
        return;
      }

      // Fondo semitransparente para "apagar" todo
      ctx.fillStyle = 'rgba(0,0,0,0.5)';
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      // Luego pintamos los píxeles de los seleccionados con más intensidad
      ctx.globalAlpha = 1.0;
      ctx.fillStyle = 'rgba(255,0,0,0.9)';

      const pixels = data.pixels;
      for (let i = 0; i < pixels.length; i++) {
        const pix = pixels[i];
        const painters = pix.painters;
        let match = false;
        for (let j = 0; j < painters.length; j++) {
          if (selected.has(painters[j])) {
            match = true;
            break;
          }
        }
        if (!match) continue;

        const x = pix.x;
        const y = pix.y;
        ctx.fillRect(x, y, 1, 1);
      }
    }
  </script>
</body>
</html>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[OK] HTML guardado en: {path}")


# ==========================
# MAIN
# ==========================

def main():
    print("[INFO] Construyendo imagen del rectángulo...")
    build_rect_image(START, END, RECT_IMAGE_PATH)

    print("[INFO] Recolectando datos de pintores...")
    painter_counts, pixel_painters = collect_paint_data(START, END)

    print("[INFO] Exportando JSON...")
    export_data_json(START, END, painter_counts, pixel_painters, DATA_JSON_PATH)

    print("[INFO] Exportando HTML...")
    export_html(HTML_PATH)

    print("[DONE] Listo. Abre el archivo index.html en la carpeta 'output' en tu navegador.")


if __name__ == "__main__":
    main()