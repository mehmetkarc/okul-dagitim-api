import threading
import time
import uuid
import traceback
from flask import Flask, request, jsonify
from flask_cors import CORS
from motor import dagit, arka_plan_arama

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
        "versiyon": "3.0.0-arka-plan-arama",
        "motor_dosya": _motor_modul.__file__,
        "asama_yapisi_var_mi": hasattr(_motor_modul, "_dagit_tek_deneme"),
        "arka_plan_arama_var_mi": hasattr(_motor_modul, "arka_plan_arama"),
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
        return jsonify({"basari": False, "hata": str(e), "detay": traceback.format_exc()}), 500


# ==================== ARKA PLAN PENCERE OPTIMIZASYONU ====================
# Web istegi 360sn ile sinirliyken, kullanicinin "Pencere Sayısını Azalt"
# butonuna basmasiyla SUNUCU TARAFINDA (Render uzerinde, kullanicinin
# bilgisayarinda DEGIL) dakikalarca/saatlerce arka planda calisan bir arama
# baslatilir. Kullanici hicbir kurulum/CMD/JSON indirme islemi yapmaz -
# sadece butona basar, ilerlemeyi sayfada gorur, istedigi an sonucu
# uygular. Bu, coklu-okul (SaaS) kullanimina uygun tek yontemdir.
#
# ONEMLI SINIRLAMA: is durumu SUNUCU BELLEGINDE (RAM) tutulur - Render
# instance'i yeniden baslarsa (deploy, uyku modundan cikma vb.) calisan
# isler kaybolur. Bu, mevcut olcek icin kabul edilebilir bir basitlestirme;
# ileride kalici bir veritabani (orn. Supabase) ile guclendirilebilir.
# Ayni anda SADECE BIR is calisir (sunucu kaynaklarini korumak icin) -
# okullar farkli Render instance'lari kullandigi surece bu okullar arasi
# bir sorun yaratmaz.

_isler_kilit = threading.Lock()
_isler = {}  # job_id -> is durumu sozlugu


def _is_calistir(job_id, veri, sure_sn, tur_butcesi_sn):
    def ilerleme(tur_no, en_iyi_sonuc, en_iyi_skor, gecen_sn):
        with _isler_kilit:
            is_kaydi = _isler.get(job_id)
            if is_kaydi is None:
                return
            ist = en_iyi_sonuc.get("istatistik", {})
            is_kaydi["tur_no"] = tur_no
            is_kaydi["gecen_sn"] = round(gecen_sn, 1)
            is_kaydi["en_iyi_sonuc"] = en_iyi_sonuc
            is_kaydi["en_iyi_ozet"] = {
                "eksik": len(en_iyi_sonuc.get("eksikler", [])),
                "min_ihlal_sayisi": ist.get("min_ihlal_sayisi"),
                "sifir_bos_gun_sayisi": ist.get("sifir_bos_gun_sayisi"),
                "fazla_bos_gun_sayisi": ist.get("fazla_bos_gun_sayisi"),
                "pencere_fazla_sayisi": ist.get("pencere_fazla_sayisi"),
                "pencere_toplam": ist.get("pencere_toplam"),
            }

    def durdur_mu():
        with _isler_kilit:
            is_kaydi = _isler.get(job_id)
            return is_kaydi is not None and is_kaydi.get("durdur_istendi")

    try:
        with _isler_kilit:
            _isler[job_id]["durum"] = "calisiyor"
        arka_plan_arama(veri, sure_sn, ilerleme_fn=ilerleme, durdur_fn=durdur_mu,
                         tur_butcesi_sn=tur_butcesi_sn)
        with _isler_kilit:
            if job_id in _isler:
                _isler[job_id]["durum"] = (
                    "durduruldu" if _isler[job_id].get("durdur_istendi") else "tamamlandi"
                )
    except Exception as e:
        with _isler_kilit:
            if job_id in _isler:
                _isler[job_id]["durum"] = "hata"
                _isler[job_id]["hata"] = str(e)


@app.route("/pencere-optimize-baslat", methods=["POST", "OPTIONS"])
def pencere_optimize_baslat():
    if request.method == "OPTIONS":
        return jsonify({}), 200
    try:
        veri = request.get_json(force=True)
        if not veri:
            return jsonify({"hata": "JSON verisi bulunamadi"}), 400

        # Ayni anda sadece bir is calissin - sunucu kaynaklarini koru
        with _isler_kilit:
            for jid, kayit in _isler.items():
                if kayit.get("durum") == "calisiyor":
                    return jsonify({
                        "hata": "Zaten calisan bir arka plan islemi var",
                        "job_id": jid,
                        "durum": "calisiyor",
                    }), 409

        sure_dakika = float(veri.pop("_sure_dakika", 20))
        sure_sn = max(30, sure_dakika * 60)
        tur_butcesi_sn = int(veri.pop("_tur_butcesi_sn", 90))

        job_id = str(uuid.uuid4())
        with _isler_kilit:
            _isler[job_id] = {
                "durum": "baslatiliyor",
                "tur_no": 0,
                "gecen_sn": 0,
                "hedef_sn": sure_sn,
                "en_iyi_sonuc": None,
                "en_iyi_ozet": None,
                "durdur_istendi": False,
                "hata": None,
                "baslangic": time.time(),
            }

        t = threading.Thread(target=_is_calistir, args=(job_id, veri, sure_sn, tur_butcesi_sn), daemon=True)
        t.start()

        return jsonify({"job_id": job_id, "durum": "baslatildi", "hedef_sn": sure_sn})
    except Exception as e:
        return jsonify({"hata": str(e), "detay": traceback.format_exc()}), 500


@app.route("/pencere-optimize-durum/<job_id>", methods=["GET"])
def pencere_optimize_durum(job_id):
    with _isler_kilit:
        kayit = _isler.get(job_id)
        if kayit is None:
            return jsonify({"durum": "bulunamadi"}), 404
        return jsonify({
            "durum": kayit["durum"],
            "tur_no": kayit["tur_no"],
            "gecen_sn": kayit["gecen_sn"],
            "hedef_sn": kayit["hedef_sn"],
            "en_iyi_ozet": kayit["en_iyi_ozet"],
            "hata": kayit["hata"],
        })


@app.route("/pencere-optimize-sonuc/<job_id>", methods=["GET"])
def pencere_optimize_sonuc(job_id):
    with _isler_kilit:
        kayit = _isler.get(job_id)
        if kayit is None:
            return jsonify({"hata": "is bulunamadi"}), 404
        if kayit["en_iyi_sonuc"] is None:
            return jsonify({"hata": "henuz tamamlanmis bir tur yok"}), 202
        return jsonify(kayit["en_iyi_sonuc"])


@app.route("/pencere-optimize-durdur", methods=["POST", "OPTIONS"])
def pencere_optimize_durdur():
    if request.method == "OPTIONS":
        return jsonify({}), 200
    veri = request.get_json(force=True) or {}
    job_id = veri.get("job_id")
    with _isler_kilit:
        kayit = _isler.get(job_id)
        if kayit is None:
            return jsonify({"hata": "is bulunamadi"}), 404
        kayit["durdur_istendi"] = True
    return jsonify({"durum": "durdurma_istendi"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
