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
    return jsonify({"durum": "aktif", "versiyon": "1.0.0"})

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
