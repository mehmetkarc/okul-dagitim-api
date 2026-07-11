"""
OkulYonetimSistemi — Dağıtım API Sunucusu
Flask + OR-Tools. Netlify frontend'inden çağrılır.
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
from motor import dagit

app = Flask(__name__)
CORS(app)  # Netlify'dan çağrı için gerekli


@app.route("/saglik", methods=["GET"])
def saglik():
    return jsonify({"durum": "aktif", "versiyon": "1.0.0"})


@app.route("/dagit", methods=["POST"])
def dagitim_yap():
    try:
        veri = request.get_json(force=True)
        if not veri:
            return jsonify({"hata": "JSON verisi bulunamadı"}), 400

        sonuc = dagit(veri)
        return jsonify(sonuc)

    except Exception as e:
        import traceback
        return jsonify({
            "basari": False,
            "hata": str(e),
            "detay": traceback.format_exc()
        }), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
