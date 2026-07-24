import json
import threading
import time
import traceback
import urllib.request
import urllib.error
import uuid
from flask import Flask, request, jsonify
from flask_cors import CORS
from motor import dagit, arka_plan_arama

app = Flask(__name__)
CORS(app, origins="*")

# ==================== SUPABASE (KALICI IS DEPOLAMA) ====================
# Arka plan islerinin durumu ONCE sunucu bellegindeki (RAM) _isler
# sozlugune yazilir (hizli okuma/yazma icin), AYRICA Supabase'e de
# kaydedilir (KALICI - Render servisi yeniden baslasa/uykuya gecse bile
# kaybolmaz). Bu tablo /mnt/user-data/outputs/supabase_arka_plan_isler.sql
# ile olusturulmali.
SUPABASE_URL = "https://uahzohwmjluldastjlay.supabase.co"
SUPABASE_ANON_KEY = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIs"
                      "InJlZiI6InVhaHpvaHdtamx1bGRhc3RqbGF5Iiwicm9sZSI6ImFub24iLCJpYXQi"
                      "OjE3NzMyNzk4NDQsImV4cCI6MjA4ODg1NTg0NH0.-EdDbpWEtpHuQRpi5IqFNkBG"
                      "lJIe8syriIDegQQC-vY")


def _supabase_istek(yol, metod="GET", gövde=None, ek_basliklar=None):
    """Supabase REST API'ye basit bir istek yapar (urllib ile - ekstra
    kutuphane gerekmez). Basarisiz olursa None doner, ANA ISI ASLA
    durdurmaz (kalici kayit basarisiz olsa bile arka plan arama devam
    etmeli - bellek ici _isler her zaman calisir durumda)."""
    try:
        url = SUPABASE_URL + yol
        veri_bytes = json.dumps(gövde).encode("utf-8") if gövde is not None else None
        istek = urllib.request.Request(url, data=veri_bytes, method=metod)
        istek.add_header("apikey", SUPABASE_ANON_KEY)
        istek.add_header("Authorization", "Bearer " + SUPABASE_ANON_KEY)
        istek.add_header("Content-Type", "application/json")
        if ek_basliklar:
            for k, v in ek_basliklar.items():
                istek.add_header(k, v)
        with urllib.request.urlopen(istek, timeout=15) as r:
            icerik = r.read()
            return json.loads(icerik) if icerik else None
    except Exception as e:
        print(f"[SUPABASE] istek hatasi ({yol}): {e}", flush=True)
        return None


def _supabase_is_kaydet(job_id, kayit):
    """Is durumunu Supabase'e KAYDEDER/GUNCELLER (upsert)."""
    gövde = dict(kayit)
    gövde["job_id"] = job_id
    gövde["guncelleme_zamani"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _supabase_istek("/rest/v1/arka_plan_isler?on_conflict=job_id", "POST", gövde,
                     ek_basliklar={"Prefer": "resolution=merge-duplicates"})


def _supabase_is_oku(job_id):
    """Is durumunu Supabase'den okur (bellek ici _isler'de bulunamazsa
    - orn. sunucu yeniden basladiysa - buradan kurtarilir)."""
    sonuc = _supabase_istek(f"/rest/v1/arka_plan_isler?job_id=eq.{job_id}&select=*")
    if sonuc and len(sonuc) > 0:
        return sonuc[0]
    return None


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
        "versiyon": "4.1.0-pencere-fazla-oncelik",
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
# baslatilir. Is durumu HEM bellekte (hizli) HEM Supabase'de (KALICI)
# tutulur - Render servisi uzun bir is sirasinda yeniden baslarsa (deploy,
# uyku modundan cikma, bakim vb.) bellek sifirlanir AMA Supabase'deki
# kayit KALIR, boylece /durum ve /sonuc endpoint'leri is bulunamadiginda
# otomatik olarak Supabase'e bakar ve KALDIGI YERDEN sonucu dondurur.
#
# Ayni anda SADECE BIR is calisir (sunucu kaynaklarini korumak icin).

_isler_kilit = threading.Lock()
_isler = {}  # job_id -> is durumu sozlugu (bellek ici, hizli erisim)


def _is_calistir(job_id, veri, sure_sn, tur_butcesi_sn, okul_kodu):
    def ilerleme(tur_no, en_iyi_sonuc, en_iyi_skor, gecen_sn):
        ist = en_iyi_sonuc.get("istatistik", {})
        ozet = {
            "eksik": len(en_iyi_sonuc.get("eksikler", [])),
            "min_ihlal_sayisi": ist.get("min_ihlal_sayisi"),
            "sifir_bos_gun_sayisi": ist.get("sifir_bos_gun_sayisi"),
            "fazla_bos_gun_sayisi": ist.get("fazla_bos_gun_sayisi"),
            "pencere_fazla_sayisi": ist.get("pencere_fazla_sayisi"),
            "pencere_max": ist.get("pencere_max"),
            "pencere_toplam": ist.get("pencere_toplam"),
        }
        with _isler_kilit:
            is_kaydi = _isler.get(job_id)
            if is_kaydi is None:
                return
            is_kaydi["tur_no"] = tur_no
            is_kaydi["gecen_sn"] = round(gecen_sn, 1)
            is_kaydi["en_iyi_sonuc"] = en_iyi_sonuc
            is_kaydi["en_iyi_ozet"] = ozet
        # KALICI KAYIT: her yeni en iyi sonuc bulundugunda Supabase'e de
        # yazilir - boylece sunucu yeniden baslasa bile EN AZ bu son iyi
        # sonuc kaybolmaz.
        _supabase_is_kaydet(job_id, {
            "okul_kodu": okul_kodu, "durum": "calisiyor", "tur_no": tur_no,
            "gecen_sn": round(gecen_sn, 1), "hedef_sn": sure_sn,
            "en_iyi_ozet": ozet, "en_iyi_sonuc": en_iyi_sonuc,
        })

    def durdur_mu():
        with _isler_kilit:
            is_kaydi = _isler.get(job_id)
            return is_kaydi is not None and is_kaydi.get("durdur_istendi")

    nihai_durum = "tamamlandi"
    try:
        with _isler_kilit:
            _isler[job_id]["durum"] = "calisiyor"
        arka_plan_arama(veri, sure_sn, ilerleme_fn=ilerleme, durdur_fn=durdur_mu,
                         tur_butcesi_sn=tur_butcesi_sn)
        en_iyi_sonuc = None
        en_iyi_ozet = None
        tur_no = 0
        with _isler_kilit:
            if job_id in _isler:
                nihai_durum = "durduruldu" if _isler[job_id].get("durdur_istendi") else "tamamlandi"
                _isler[job_id]["durum"] = nihai_durum
                en_iyi_sonuc = _isler[job_id].get("en_iyi_sonuc")
                en_iyi_ozet = _isler[job_id].get("en_iyi_ozet")
                tur_no = _isler[job_id].get("tur_no", 0)
        _supabase_is_kaydet(job_id, {
            "okul_kodu": okul_kodu, "durum": nihai_durum, "tur_no": tur_no,
            "hedef_sn": sure_sn, "en_iyi_ozet": en_iyi_ozet, "en_iyi_sonuc": en_iyi_sonuc,
        })
    except Exception as e:
        with _isler_kilit:
            if job_id in _isler:
                _isler[job_id]["durum"] = "hata"
                _isler[job_id]["hata"] = str(e)
        _supabase_is_kaydet(job_id, {"okul_kodu": okul_kodu, "durum": "hata", "hata": str(e)})


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
        okul_kodu = veri.get("_okul_kodu") or "bilinmiyor"

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
        _supabase_is_kaydet(job_id, {
            "okul_kodu": okul_kodu, "durum": "baslatiliyor", "tur_no": 0, "hedef_sn": sure_sn,
        })

        t = threading.Thread(target=_is_calistir,
                              args=(job_id, veri, sure_sn, tur_butcesi_sn, okul_kodu), daemon=True)
        t.start()

        return jsonify({"job_id": job_id, "durum": "baslatildi", "hedef_sn": sure_sn})
    except Exception as e:
        return jsonify({"hata": str(e), "detay": traceback.format_exc()}), 500


@app.route("/pencere-optimize-durum/<job_id>", methods=["GET"])
def pencere_optimize_durum(job_id):
    with _isler_kilit:
        kayit = _isler.get(job_id)
        if kayit is not None:
            return jsonify({
                "durum": kayit["durum"],
                "tur_no": kayit["tur_no"],
                "gecen_sn": kayit["gecen_sn"],
                "hedef_sn": kayit["hedef_sn"],
                "en_iyi_ozet": kayit["en_iyi_ozet"],
                "hata": kayit["hata"],
            })
    # Bellekte yok (sunucu yeniden baslamis olabilir) - Supabase'e bak
    sb_kayit = _supabase_is_oku(job_id)
    if sb_kayit:
        return jsonify({
            "durum": sb_kayit.get("durum", "bulunamadi"),
            "tur_no": sb_kayit.get("tur_no", 0),
            "gecen_sn": sb_kayit.get("gecen_sn"),
            "hedef_sn": sb_kayit.get("hedef_sn"),
            "en_iyi_ozet": sb_kayit.get("en_iyi_ozet"),
            "hata": sb_kayit.get("hata"),
            "_kaynak": "supabase_kurtarma",
        })
    return jsonify({"durum": "bulunamadi"}), 404


@app.route("/pencere-optimize-sonuc/<job_id>", methods=["GET"])
def pencere_optimize_sonuc(job_id):
    with _isler_kilit:
        kayit = _isler.get(job_id)
        if kayit is not None and kayit.get("en_iyi_sonuc") is not None:
            return jsonify(kayit["en_iyi_sonuc"])
    # Bellekte yok/eksik - Supabase'e bak (sunucu yeniden baslamis olabilir)
    sb_kayit = _supabase_is_oku(job_id)
    if sb_kayit and sb_kayit.get("en_iyi_sonuc"):
        return jsonify(sb_kayit["en_iyi_sonuc"])
    return jsonify({"hata": "henuz tamamlanmis bir tur yok veya is bulunamadi"}), 202


@app.route("/pencere-optimize-durdur", methods=["POST", "OPTIONS"])
def pencere_optimize_durdur():
    if request.method == "OPTIONS":
        return jsonify({}), 200
    veri = request.get_json(force=True) or {}
    job_id = veri.get("job_id")
    with _isler_kilit:
        kayit = _isler.get(job_id)
        if kayit is not None:
            kayit["durdur_istendi"] = True
            return jsonify({"durum": "durdurma_istendi"})
    # Bellekte yok - is muhtemelen sunucu yeniden baslamadan once bitmis/
    # kaybolmus. Supabase'deki son duruma bakip kullaniciya bildir.
    sb_kayit = _supabase_is_oku(job_id)
    if sb_kayit:
        return jsonify({"durum": sb_kayit.get("durum", "bilinmiyor"),
                         "not": "is artik bu sunucu orneginde aktif degil (muhtemelen sunucu yeniden basladi) - "
                                "Supabase'deki en son kayitli sonuc kullanilabilir."})
    return jsonify({"hata": "is bulunamadi"}), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
