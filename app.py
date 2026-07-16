from flask import Flask, request, jsonify
from flask_cors import CORS
from motor import dagit
app = Flask(__name__)
CORS(app, origins="*")
@app.after_request
def cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    return response
@app.route("/saglik", methods=["GET"])
def saglik():
    import motor as _motor_modul
    return jsonify({
        "durum": "aktif",
        "versiyon": "2.0.0-asama-guvenlik",
        "motor_dosya": _motor_modul.__file__,
        "asama_yapisi_var_mi": hasattr(_motor_modul, "_dagit_tek_deneme"),
    })
@app.route("/debug", methods=["POST"])
def debug_veri():
    """Frontend'den gelen kisitlari gosterir - brans/unvan gelip gelmedigini de kontrol eder"""
    veri = request.get_json(force=True)
    kisitlar = veri.get("kisitlar", {})
    ornek = {}
    brans_olan = 0
    unvan_olan = 0
    for tc, k in kisitlar.items():
        if k.get("brans"):
            brans_olan += 1
        if k.get("unvan"):
            unvan_olan += 1
    for i, (tc, k) in enumerate(list(kisitlar.items())[:5]):
        ornek[tc[-6:]] = k
    return jsonify({
        "kisit_sayisi": len(kisitlar),
        "brans_gelen_ogretmen_sayisi": brans_olan,
        "unvan_gelen_ogretmen_sayisi": unvan_olan,
        "ornek": ornek,
    })
@app.route("/dagit", methods=["POST", "OPTIONS"])
def dagitim_yap():
    if request.method == "OPTIONS":
        return jsonify({}), 200
    try:
        veri = request.get_json(force=True)
        if not veri:
            return jsonify({"hata": "JSON verisi bulunamadi"}), 400
        sonuc = dagit(veri)
        return jsonify(sonuc)
    except Exception as e:
        import traceback
        return jsonify({"basari": False, "hata": str(e), "detay": traceback.format_exc()}), 500
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
