from flask import Flask, render_template, request, jsonify, send_file
import sqlite3
import pandas as pd
import io
import os
from datetime import datetime
from werkzeug.utils import secure_filename

app = Flask(__name__)

DB_NAME = "sbks.db"
UPLOAD_FOLDER = "uploads"
BACKUP_FOLDER = "backup"
ALLOWED_EXTENSIONS = {"csv"}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(BACKUP_FOLDER, exist_ok=True)


# =========================
# VALIDASI FILE
# =========================
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# =========================
# DATABASE CONNECTION
# =========================
def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


# =========================
# HALAMAN UTAMA
# =========================
@app.route("/")
def index():
    conn = get_db()
    cur = conn.cursor()

    kedeputian = [
        r["kedeputian"] for r in cur.execute("""
            SELECT DISTINCT kedeputian
            FROM sbks
            WHERE kedeputian IS NOT NULL
              AND kedeputian != ''
              AND UPPER(kabkota) NOT LIKE 'PROVINSI%'
            ORDER BY kedeputian
        """).fetchall()
    ]

    provinsi = [
        r["provinsi"] for r in cur.execute("""
            SELECT DISTINCT provinsi
            FROM sbks
            WHERE provinsi IS NOT NULL
              AND provinsi != ''
              AND UPPER(kabkota) NOT LIKE 'PROVINSI%'
            ORDER BY provinsi
        """).fetchall()
    ]

    conn.close()

    return render_template(
        "index.html",
        kedeputian_list=kedeputian,
        provinsi_list=provinsi
    )


# =========================
# DROPDOWN KEGIATAN
# =========================
@app.route("/api/kegiatan")
def api_kegiatan():
    kedeputian = request.args.get("kedeputian")

    conn = get_db()
    cur = conn.cursor()

    if kedeputian:
        rows = cur.execute("""
            SELECT DISTINCT kegiatan
            FROM sbks
            WHERE kedeputian=?
              AND UPPER(kabkota) NOT LIKE 'PROVINSI%'
            ORDER BY kegiatan
        """, (kedeputian,)).fetchall()
    else:
        rows = cur.execute("""
            SELECT DISTINCT kegiatan
            FROM sbks
            WHERE UPPER(kabkota) NOT LIKE 'PROVINSI%'
            ORDER BY kegiatan
        """).fetchall()

    conn.close()
    return jsonify([r["kegiatan"] for r in rows])


# =========================
# DROPDOWN KABKOTA
# =========================
@app.route("/api/kabkota")
def api_kabkota():
    provinsi = request.args.get("provinsi")

    conn = get_db()
    cur = conn.cursor()

    if provinsi:
        rows = cur.execute("""
            SELECT DISTINCT kabkota
            FROM sbks
            WHERE provinsi=?
              AND UPPER(kabkota) NOT LIKE 'PROVINSI%'
            ORDER BY kabkota
        """, (provinsi,)).fetchall()
    else:
        rows = cur.execute("""
            SELECT DISTINCT kabkota
            FROM sbks
            WHERE UPPER(kabkota) NOT LIKE 'PROVINSI%'
            ORDER BY kabkota
        """).fetchall()

    conn.close()
    return jsonify([r["kabkota"] for r in rows])


# =========================
# PERATURAN
# =========================
@app.route("/api/peraturan")
def api_peraturan():
    kegiatan = request.args.get("kegiatan")

    if not kegiatan:
        return jsonify("")

    conn = get_db()
    cur = conn.cursor()

    row = cur.execute("""
        SELECT DISTINCT peraturan
        FROM sbks
        WHERE kegiatan=?
          AND UPPER(kabkota) NOT LIKE 'PROVINSI%'
    """, (kegiatan,)).fetchone()

    conn.close()

    return jsonify(row["peraturan"] if row else "")


# =========================
# SEARCH DATA
# =========================
@app.route("/api/search", methods=["POST"])
def api_search():
    data = request.get_json() or {}

    base_query = " FROM sbks WHERE UPPER(kabkota) NOT LIKE 'PROVINSI%' "
    params = []

    filters = ["kedeputian", "kegiatan", "provinsi", "kabkota", "kategori"]

    for f in filters:
        if data.get(f):
            base_query += f" AND {f}=?"
            params.append(data.get(f))

    conn = get_db()
    cur = conn.cursor()

    columns_info = cur.execute("PRAGMA table_info(sbks)").fetchall()
    column_order = [col["name"] for col in columns_info]

    rows = cur.execute(
        "SELECT * " + base_query + " ORDER BY provinsi, kabkota",
        params
    ).fetchall()

    conn.close()

    return jsonify({
        "columns": column_order,
        "data": [dict(r) for r in rows]
    })


# =========================
# EXPORT EXCEL
# =========================
from datetime import datetime
import re

@app.route("/api/export")
def api_export():

    params = request.args
    query = "SELECT * FROM sbks WHERE UPPER(kabkota) NOT LIKE 'PROVINSI%'"
    values = []

    filters = ["kedeputian", "kegiatan", "provinsi", "kabkota", "kategori"]
    active_tags = []

    for f in filters:
        if params.get(f):
            query += f" AND {f}=?"
            values.append(params.get(f))
            active_tags.append(params.get(f))

    conn = get_db()
    df = pd.read_sql_query(query, conn, params=values)
    conn.close()

    output = io.BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)

    # === FORMAT NAMA FILE ===
    now = datetime.now().strftime("%d-%m-%Y_%H-%M")

    if active_tags:
        filename_base = "_".join(active_tags)
    else:
        filename_base = "Semua_Data"

    # bersihkan karakter ilegal
    filename_base = re.sub(r"[^\w\-]", "_", filename_base)

    filename = f"SBKS_{filename_base}_{now}.xlsx"

    return send_file(
        output,
        download_name=filename,
        as_attachment=True
    )


# =========================
# IMPORT CSV VIA WEB
# =========================
@app.route("/admin/import", methods=["GET", "POST"])
def import_csv():

    message = ""

    if request.method == "POST":

        if "file" not in request.files:
            message = "File tidak ditemukan."
            return render_template("import.html", message=message)

        file = request.files["file"]

        if file.filename == "":
            message = "Tidak ada file dipilih."
            return render_template("import.html", message=message)

        if file and allowed_file(file.filename):

            filename = secure_filename(file.filename)
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            file.save(filepath)

            try:
                # Backup database lama
                if os.path.exists(DB_NAME):
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    backup_path = os.path.join(
                        BACKUP_FOLDER,
                        f"sbks_backup_{timestamp}.db"
                    )
                    os.replace(DB_NAME, backup_path)

                # Import CSV
                df = pd.read_csv(filepath)

                conn = sqlite3.connect(DB_NAME)
                df.to_sql("sbks", conn, if_exists="replace", index=False)
                conn.close()

                message = "Import CSV berhasil dan database diperbarui."

            except Exception as e:
                message = f"Terjadi kesalahan: {str(e)}"

        else:
            message = "Format file harus CSV."

    return render_template("import.html", message=message)


# =========================
# MAIN
# =========================
import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)