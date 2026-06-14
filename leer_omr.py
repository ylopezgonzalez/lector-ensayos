#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lector de hojas de respuesta OMR (recuadros, no burbujas).

Flujo:
  1. Carga la imagen (foto de celular o escaner).
  2. Detecta las 4 marcas de registro (cuadrados negros de las esquinas)
     y corrige la perspectiva.
  3. Detecta las casillas de opcion (A, B, C, D) agrupandolas en filas.
  4. Para cada fila mide el relleno de cada casilla y decide la marca.
     - Filas sin 4 casillas validas se consideran "desarrollo" (cuadernillo).
  5. Vuelca el resultado horizontalmente a un Google Sheet:
     columna 1 = nombre del estudiante, columna 2 en adelante = respuestas.

Cantidad de preguntas variable (hasta 40+). Las preguntas de desarrollo
("Responder en el cuadernillo") pueden estar en cualquier posicion y ser
mas de una; se registran como "DES".

Uso:
  python leer_omr.py imagen.jpg --nombre "Juan Perez"
  python leer_omr.py carpeta/ --batch            # procesa todas las imagenes
  python leer_omr.py imagen.jpg --debug          # guarda imagenes de diagnostico
  python leer_omr.py imagen.jpg --no-sheets      # no vuelca, solo imprime/CSV

Dependencias:
  pip install opencv-python numpy gspread google-auth
"""

import os
import sys
import csv
import glob
import argparse

import cv2
import numpy as np

# ----------------------------------------------------------------------------
# CONFIGURACION
# ----------------------------------------------------------------------------

SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/12pOrGQGusU_AnXN-eR69fcW0oYvQeqzqJVyv6rQumpE/edit#gid=0"
WORKSHEET_NAME = "Hoja 1"          # nombre de la pestana destino
SERVICE_ACCOUNT_FILE = "credentials.json"  # JSON de la cuenta de servicio

OPTIONS = ["A", "B", "C", "D"]     # opciones por pregunta (en orden de izq a der)

# Tamano al que se endereza la hoja (proporcion A4 vertical).
WARP_W = 1000
WARP_H = int(WARP_W * 297 / 210)   # ~1414

# Umbrales de deteccion de marca:
FILL_THRESHOLD = 0.10   # fraccion minima de pixeles oscuros para considerar "marcada"
AMBIGUOUS_RATIO = 0.55  # si la 2da casilla mas oscura >= 55% de la 1ra -> ambiguo

EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")


# ----------------------------------------------------------------------------
# 1) PRE-PROCESO Y CORRECCION DE PERSPECTIVA
# ----------------------------------------------------------------------------

def order_points(pts):
    """Ordena 4 puntos como [sup-izq, sup-der, inf-der, inf-izq]."""
    pts = np.array(pts, dtype="float32")
    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).ravel()
    return np.array([
        pts[np.argmin(s)],      # sup-izq (menor x+y)
        pts[np.argmin(diff)],   # sup-der (menor y-x -> x grande, y chico)
        pts[np.argmax(s)],      # inf-der (mayor x+y)
        pts[np.argmax(diff)],   # inf-izq (mayor y-x)
    ], dtype="float32")


def find_fiducials(gray):
    """
    Busca 4 marcas de registro (cuadrados negros solidos) cercanas a las
    esquinas. Devuelve sus centros ordenados o None si no encuentra 4.
    """
    h, w = gray.shape
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]

    cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    img_area = h * w
    candidates = []
    for ctr in cnts:
        area = cv2.contourArea(ctr)
        # Las fiduciales son pequenas respecto a la pagina pero no ruido.
        if area < img_area * 0.00015 or area > img_area * 0.02:
            continue
        x, y, bw, bh = cv2.boundingRect(ctr)
        ar = bw / float(bh)
        if not (0.6 <= ar <= 1.6):          # aprox cuadrada
            continue
        rect_area = bw * bh
        if rect_area == 0 or area / rect_area < 0.7:  # solida (relleno)
            continue
        cx, cy = x + bw / 2.0, y + bh / 2.0
        candidates.append((cx, cy, area))

    if len(candidates) < 4:
        return None

    # Quedarnos con el candidato mas cercano a cada esquina.
    corners = [(0, 0), (w, 0), (w, h), (0, h)]
    chosen = []
    used = set()
    for corner in corners:
        best, best_d = None, None
        for i, (cx, cy, _) in enumerate(candidates):
            if i in used:
                continue
            d = (cx - corner[0]) ** 2 + (cy - corner[1]) ** 2
            if best_d is None or d < best_d:
                best_d, best = d, i
        if best is None:
            return None
        used.add(best)
        chosen.append((candidates[best][0], candidates[best][1]))

    return order_points(chosen)


def fallback_page_contour(gray):
    """
    Si no hay fiduciales, intenta detectar el rectangulo de la hoja
    (el contorno cuadrilatero mas grande) como respaldo.
    """
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edged = cv2.Canny(blur, 50, 150)
    edged = cv2.dilate(edged, np.ones((3, 3), np.uint8), iterations=1)
    cnts, _ = cv2.findContours(edged, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    cnts = sorted(cnts, key=cv2.contourArea, reverse=True)[:5]
    for ctr in cnts:
        peri = cv2.arcLength(ctr, True)
        approx = cv2.approxPolyDP(ctr, 0.02 * peri, True)
        if len(approx) == 4:
            return order_points(approx.reshape(4, 2))
    return None


def deskew(image):
    """Devuelve la hoja enderezada (vista cenital) a tamano fijo."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    quad = find_fiducials(gray)
    method = "fiduciales"
    if quad is None:
        quad = fallback_page_contour(gray)
        method = "contorno de pagina"
    if quad is None:
        # ultimo recurso: usar la imagen completa
        h, w = gray.shape
        quad = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype="float32")
        method = "imagen completa (sin correccion)"

    dst = np.array([[0, 0], [WARP_W, 0], [WARP_W, WARP_H], [0, WARP_H]],
                   dtype="float32")
    M = cv2.getPerspectiveTransform(quad, dst)
    warped = cv2.warpPerspective(image, M, (WARP_W, WARP_H))
    return warped, method


# ----------------------------------------------------------------------------
# 2) DETECCION DE CASILLAS Y AGRUPACION EN FILAS
# ----------------------------------------------------------------------------

def detect_boxes(warped):
    """
    Detecta las casillas de opcion (recuadros vacios) en la hoja enderezada.
    Devuelve lista de (x, y, w, h).
    """
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    th = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY_INV, 25, 10)

    cnts, hierarchy = cv2.findContours(th, cv2.RETR_LIST,
                                       cv2.CHAIN_APPROX_SIMPLE)

    page_area = WARP_W * WARP_H
    boxes = []
    for ctr in cnts:
        area = cv2.contourArea(ctr)
        # Las casillas son un rango acotado del area de la pagina.
        if area < page_area * 0.0004 or area > page_area * 0.01:
            continue
        peri = cv2.arcLength(ctr, True)
        approx = cv2.approxPolyDP(ctr, 0.04 * peri, True)
        if len(approx) != 4:
            continue
        x, y, w, h = cv2.boundingRect(approx)
        ar = w / float(h)
        if not (0.7 <= ar <= 1.4):          # casi cuadrada
            continue
        boxes.append((x, y, w, h))

    boxes = dedup_boxes(boxes)
    return boxes


def dedup_boxes(boxes, tol=12):
    """Elimina casillas duplicadas (contornos interno/externo del borde)."""
    kept = []
    for b in sorted(boxes, key=lambda r: r[2] * r[3], reverse=True):
        bx, by, bw, bh = b
        bcx, bcy = bx + bw / 2, by + bh / 2
        dup = False
        for k in kept:
            kx, ky, kw, kh = k
            kcx, kcy = kx + kw / 2, ky + kh / 2
            if abs(bcx - kcx) < tol and abs(bcy - kcy) < tol:
                dup = True
                break
        if not dup:
            kept.append(b)
    return kept


def group_into_rows(boxes, y_tol_factor=0.6):
    """
    Agrupa casillas por fila usando su coordenada y, y ordena cada fila por x.
    Devuelve lista de filas; cada fila es lista de (x, y, w, h) ordenada.
    """
    if not boxes:
        return []
    avg_h = np.median([b[3] for b in boxes])
    y_tol = avg_h * y_tol_factor

    boxes_sorted = sorted(boxes, key=lambda b: b[1])
    rows = []
    current = [boxes_sorted[0]]
    current_y = boxes_sorted[0][1]
    for b in boxes_sorted[1:]:
        if abs(b[1] - current_y) <= y_tol:
            current.append(b)
        else:
            rows.append(current)
            current = [b]
        current_y = np.mean([c[1] for c in current])
    rows.append(current)

    for r in rows:
        r.sort(key=lambda b: b[0])
    return rows


def split_columns(rows):
    """
    La hoja tiene dos columnas de preguntas. Detecta el corte horizontal y
    devuelve las filas ordenadas como se leen: primero toda la columna
    izquierda (de arriba abajo), luego la derecha.
    Solo aplica si las filas mezclan ambas columnas.
    """
    # Si cada fila ya tiene <= len(OPTIONS) casillas, no hay dos columnas
    # mezcladas en una sola fila; aun asi puede haber dos bloques verticales.
    all_x = [b[0] for row in rows for b in row]
    if not all_x:
        return rows
    x_min, x_max = min(all_x), max(all_x)
    mid = (x_min + x_max) / 2

    # Caso A: filas que contienen casillas de ambas columnas (8 casillas).
    left_rows, right_rows = [], []
    mixed = any(len(r) > len(OPTIONS) + 1 for r in rows)
    if mixed:
        for r in rows:
            left = [b for b in r if b[0] + b[2] / 2 < mid]
            right = [b for b in r if b[0] + b[2] / 2 >= mid]
            if left:
                left_rows.append(sorted(left, key=lambda b: b[0]))
            if right:
                right_rows.append(sorted(right, key=lambda b: b[0]))
        return left_rows + right_rows

    # Caso B: cada fila pertenece a una sola columna. Separar por x del 1er box.
    col_left = [r for r in rows if r[0][0] + r[0][2] / 2 < mid]
    col_right = [r for r in rows if r[0][0] + r[0][2] / 2 >= mid]
    if col_left and col_right:
        col_left.sort(key=lambda r: r[0][1])
        col_right.sort(key=lambda r: r[0][1])
        return col_left + col_right

    # Una sola columna.
    return sorted(rows, key=lambda r: r[0][1])


# ----------------------------------------------------------------------------
# 3) LECTURA DE MARCAS
# ----------------------------------------------------------------------------

def fill_ratio(gray_bin, box, inset=0.18):
    """Fraccion de pixeles oscuros dentro de la casilla (con margen interno)."""
    x, y, w, h = box
    dx, dy = int(w * inset), int(h * inset)
    roi = gray_bin[y + dy:y + h - dy, x + dx:x + w - dx]
    if roi.size == 0:
        return 0.0
    return float(np.count_nonzero(roi)) / roi.size


def compute_column_anchors(rows, n_opts=len(OPTIONS)):
    """
    Calcula las posiciones x de las columnas de opcion (A, B, C, D) usando
    TODAS las filas de un bloque. Como las casillas estan alineadas en columnas,
    agrupamos sus centros x en n_opts grupos. Esto permite reconstruir una
    casilla aunque una marca (X) haya roto su contorno en una fila puntual.

    Devuelve (anchors_x, box_w, box_h): centros x de cada columna y tamano tipico.
    """
    all_boxes = [b for r in rows for b in r]
    if len(all_boxes) < n_opts:
        return None, None, None

    ws = sorted(b[2] for b in all_boxes)
    hs = sorted(b[3] for b in all_boxes)
    mw = ws[len(ws) // 2]
    mh = hs[len(hs) // 2]

    centers = sorted(b[0] + b[2] / 2.0 for b in all_boxes)
    # Agrupar centros en columnas: un salto mayor a ~media casilla = nueva columna.
    gap = mw * 0.6
    clusters = [[centers[0]]]
    for cx in centers[1:]:
        if cx - clusters[-1][-1] <= gap:
            clusters[-1].append(cx)
        else:
            clusters.append([cx])
    # Centro de cada columna = mediana del cluster; nos quedamos con los n_opts
    # clusters mas poblados (las verdaderas columnas de opcion).
    clusters.sort(key=len, reverse=True)
    main = clusters[:n_opts]
    anchors = sorted(float(np.median(c)) for c in main)
    if len(anchors) < n_opts:
        return None, None, None
    return anchors, int(mw), int(mh)


def boxes_from_anchors(row, anchors, mw, mh):
    """Construye una casilla por cada columna ancla, a la altura y de la fila."""
    if not row:
        return None
    y0 = int(np.median([b[1] for b in row]))
    return [(int(ax - mw / 2), y0, mw, mh) for ax in anchors]


def read_row(gray_bin, row, anchors, mw, mh, n_opts=len(OPTIONS)):
    """
    Lee una fila usando las columnas ancla globales. Devuelve la letra marcada,
    'DES' si es desarrollo, 'BLANCO' si no hay marca, o 'AMBIGUO'.
    """
    # Una fila de desarrollo tiene muy pocas casillas de tamano coherente.
    coherent = [b for b in row if 0.6 * mw <= b[2] <= 1.6 * mw
                and 0.6 * mh <= b[3] <= 1.6 * mh]
    if len(coherent) < max(2, n_opts - 1):
        return "DES"

    opt_boxes = boxes_from_anchors(row, anchors, mw, mh)
    ratios = [fill_ratio(gray_bin, b) for b in opt_boxes]
    order = np.argsort(ratios)[::-1]
    top = ratios[order[0]]
    second = ratios[order[1]] if len(ratios) > 1 else 0.0

    if top < FILL_THRESHOLD:
        return "BLANCO"
    if second >= top * AMBIGUOUS_RATIO and second >= FILL_THRESHOLD:
        return "AMBIGUO"
    return OPTIONS[order[0]]


def read_sheet(warped, debug_path=None, top_exclude_frac=0.20):
    """
    Procesa la hoja enderezada y devuelve lista de respuestas en orden.

    top_exclude_frac: fraccion superior de la hoja que se ignora, para que la
    banda del 'N de lista' (recuadros de digitos arriba) no se confunda con la
    pregunta 1. Las preguntas siempre estan por debajo de esa banda.
    """
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    bin_inv = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                    cv2.THRESH_BINARY_INV, 35, 15)

    H = warped.shape[0]
    y_min = H * top_exclude_frac

    boxes = detect_boxes(warped)
    # Descartar casillas en la banda superior (N de lista, encabezados).
    boxes = [b for b in boxes if b[1] >= y_min]
    rows = group_into_rows(boxes)
    rows = split_columns(rows)

    # Separar las filas en bloque izquierdo y derecho (cada uno con sus propias
    # columnas ancla). Usamos la x media de cada fila contra la mitad de pagina.
    left_rows, right_rows = [], []
    for r in rows:
        mx = np.median([b[0] + b[2] / 2 for b in r])
        (left_rows if mx < WARP_W / 2 else right_rows).append(r)

    answers = []
    dbg = warped.copy() if debug_path else None

    for block in (left_rows, right_rows):
        if not block:
            continue
        anchors, mw, mh = compute_column_anchors(block)
        for r in block:
            if anchors is None:
                ans = "DES"
                drawn = r
            else:
                ans = read_row(bin_inv, r, anchors, mw, mh)
                drawn = (boxes_from_anchors(r, anchors, mw, mh)
                         if ans in OPTIONS or ans in ("BLANCO", "AMBIGUO")
                         else r)
            answers.append(ans)
            if dbg is not None:
                for b in drawn:
                    x, y, w, h = b
                    col = (0, 200, 0) if ans in OPTIONS else (0, 140, 255)
                    cv2.rectangle(dbg, (x, y), (x + w, y + h), col, 2)

    if debug_path and dbg is not None:
        cv2.imwrite(debug_path, dbg)

    return answers


# ----------------------------------------------------------------------------
# 4) GOOGLE SHEETS
# ----------------------------------------------------------------------------

def append_to_sheet(student_name, answers):
    """Agrega una fila: [nombre, resp1, resp2, ...] al Google Sheet."""
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE,
                                                  scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_url(SPREADSHEET_URL)
    try:
        ws = sh.worksheet(WORKSHEET_NAME)
    except Exception:
        ws = sh.sheet1
    celdas = ["" if a == "BLANCO" else a for a in answers]
    ws.append_row([student_name] + celdas, value_input_option="RAW")


def write_csv(rows, path="resultados.csv"):
    """
    Escribe el CSV en formato HORIZONTAL: una fila por alumno.
      - Columna 1: nombre/numero del alumno.
      - Columnas siguientes: P1, P2, P3, ... con sus respuestas.
    'BLANCO' se escribe como celda vacia.
    """
    max_q = max((len(r[1]) for r in rows), default=0)
    header = ["Nombre"] + [f"P{i+1}" for i in range(max_q)]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for name, ans in rows:
            # BLANCO -> celda vacia; el resto (A/B/C/D/DES/AMBIGUO) tal cual.
            celdas = ["" if a == "BLANCO" else a for a in ans]
            w.writerow([name] + celdas)
    print(f"CSV guardado en {path}")


# ----------------------------------------------------------------------------
# 5) PROCESAMIENTO DE ARCHIVOS
# ----------------------------------------------------------------------------

def process_image(path, debug=False):
    image = cv2.imread(path)
    if image is None:
        raise ValueError(f"No se pudo abrir la imagen: {path}")
    warped, method = deskew(image)
    dbg_path = None
    if debug:
        base = os.path.splitext(os.path.basename(path))[0]
        dbg_path = f"debug_{base}.png"
        cv2.imwrite(f"warped_{base}.png", warped)
    answers = read_sheet(warped, debug_path=dbg_path)
    return answers, method


def main():
    ap = argparse.ArgumentParser(description="Lector OMR -> Google Sheets")
    ap.add_argument("entrada", help="Imagen o carpeta (con --batch)")
    ap.add_argument("--nombre", default="", help="Nombre del estudiante")
    ap.add_argument("--batch", action="store_true",
                    help="Procesar todas las imagenes de la carpeta")
    ap.add_argument("--no-sheets", action="store_true",
                    help="No volcar a Google Sheets (solo CSV/consola)")
    ap.add_argument("--debug", action="store_true",
                    help="Guardar imagenes de diagnostico")
    args = ap.parse_args()

    targets = []
    if args.batch:
        files = [f for f in glob.glob(os.path.join(args.entrada, "*"))
                 if f.lower().endswith(EXTENSIONS)]

        def sort_key(f):
            # Si el nombre es un numero (1, 2, ... 30) ordena numericamente;
            # si no, ordena alfabeticamente.
            base = os.path.splitext(os.path.basename(f))[0]
            return (0, int(base)) if base.isdigit() else (1, base.lower())

        files.sort(key=sort_key)
        for f in files:
            # nombre del estudiante = nombre del archivo sin extension
            name = os.path.splitext(os.path.basename(f))[0]
            targets.append((f, name))
    else:
        name = args.nombre or os.path.splitext(os.path.basename(args.entrada))[0]
        targets.append((args.entrada, name))

    results = []
    for path, name in targets:
        try:
            answers, method = process_image(path, debug=args.debug)
        except Exception as e:
            print(f"[ERROR] {path}: {e}")
            continue
        print(f"\n{name}  ({os.path.basename(path)})  [correccion: {method}]")
        print("  " + "  ".join(f"P{i+1}={a}" for i, a in enumerate(answers)))
        results.append((name, answers))

        if not args.no_sheets:
            try:
                append_to_sheet(name, answers)
                print("  -> volcado a Google Sheets OK")
            except Exception as e:
                print(f"  -> [Sheets ERROR] {e}")

    if results:
        write_csv(results)


if __name__ == "__main__":
    main()
