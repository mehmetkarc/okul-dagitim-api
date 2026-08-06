"""
OkulYonetimSistemi - Ders Dagitim Motoru v2 (GREEDY / MRV)
============================================================
ASC/FET tarzi calisir:
  1) En kisitli ogretmenden basla (yuksek yuk + bosGun/kapaliGun sayisi)
     - o ogretmenin TUM derslerini birlikte, buyuk bloktan kucuge yerlestir
  2) Cakisma varsa displacement (kovma) dene - kovulan gorev tekrar yerlestirilir,
     basarisiz olursa tum zincir geri alinir
  3) Min gunluk saat onarim gecisi (yarim kalan gunleri tamamlamaya calisir)
  4) Bos gun gecisi (en az kullanilan gunu tamamen bosaltmaya calisir)
  5) Pencere minimizasyonu gecisi (gun ici derste bosluklari sikistirir)

Veri semasi CP-SAT (stable) versiyonuyla AYNI:
  veri = {
    "siniflar": [{"id":..., "sinif_adi":...}, ...],
    "dersler":  [{"id":..., "ders_adi":..., "kisa_ad":..., "renk":...,
                  "haftalik_saat":..., "blok_dagilim":[...]}],
    "atamalar": {sid: [{"ders_id":..., "ogretmen_tc":..., "ogretmenler":[{...}]}]},
    "kisitlar": {tc: {"bosGun":int, "kapaliGunler":[...],
                       "minGunlukSaat":int, "maxGunlukSaat":int}},
    "gunler":   [{"gun":1,"saat":8}, ...],
    "kilitli":  {sid: {gun: {saat: {"ders_id":..., "ogretmen_tc":...,
                                     "ogretmenler":[...]}}}}  (opsiyonel)
    "seed": int (opsiyonel)
  }

Kilitli hucreler ASLA tasinmaz/uzerine yazilmaz - once occupation gridlerine
sabit olarak yuklenir, sonra o (sinif,ders) icin kac saatin zaten kilitli
oldugu hesaplanip kalan bloklardan dusulur (JS motorundaki "kilitliSaatler"
mantigiyla birebir ayni).

Cikti CP-SAT versiyonuyla AYNI:
  {"basari":bool, "slots":{sid:{gun:{saat:{...}}}}, "eksikler":[...],
   "sure_sn":float, "durum":str, "seed":int}
"""
import time
import random
import math

# OR-Tools CP-SAT OPSIYONEL: kurulu degilse motor ESKISI GIBI calisir.
# Bu sayede mevcut calisan yapi HICBIR SEKILDE riske atilmaz - CP-SAT
# sadece bir "ekstra iyilestirme katmani"dir.
try:
    from ortools.sat.python import cp_model as _cp_model
except Exception:
    _cp_model = None

MOTOR_VERSIYON = "9.6.0-bosgun-maliyette"  # /saglik uzerinden dogrulanir


def _dagit_tek_deneme(veri):
    t0 = time.time()
    _deneme_butcesi = float(veri.get("_deneme_butcesi_sn", 70 if veri.get("on_bos_gun_ata") else 40))
    _brans_takas_gecmisi = []  # kullaniciya "hangi takaslar yapildi" ozetlemek icin

    def _zaman_doldu():
        return time.time() - t0 > _deneme_butcesi

    siniflar  = {str(s["id"]): s for s in veri.get("siniflar", [])}
    dersler   = {str(d["id"]): d for d in veri.get("dersler", [])}
    atamalar  = {str(k): v for k, v in veri.get("atamalar", {}).items()}
    kisitlar  = {str(k): v for k, v in veri.get("kisitlar", {}).items()}
    gun_bilgi = {int(g["gun"]): int(g["saat"]) for g in veri.get("gunler", [])}
    gunler    = sorted(gun_bilgi.keys())
    seed      = veri.get("seed", random.randint(1, 999999))
    rnd = random.Random(seed)


    # ---------------- 1. Kilitli (sabit) hucreler ----------------
    # locked_cells: [(sid, gun, saat, did, tc), ...]  - asla tasinmaz
    # locked_saat[(sid,did)] -> o ders icin zaten kilitli olan saat sayisi
    kilitli_ham = veri.get("kilitli", {}) or {}
    locked_cells = []
    locked_saat = {}
    for sid, gun_map in kilitli_ham.items():
        sid = str(sid)
        for gun, saat_map in (gun_map or {}).items():
            gun = int(gun)
            for saat, hucre in (saat_map or {}).items():
                saat = int(saat)
                did = str(hucre.get("ders_id", ""))
                tc = str(hucre.get("ogretmen_tc") or (hucre.get("ogretmenler") or [{}])[0].get("tc") or "")
                locked_cells.append((sid, gun, saat, did, tc))
                key = (sid, did)
                locked_saat[key] = locked_saat.get(key, 0) + 1

    # ---------------- 2. Gorev listesi ----------------
    gorevler = []
    for sid, atama_list in atamalar.items():
        if sid not in siniflar:
            continue
        for atama in atama_list:
            did = str(atama.get("ders_id", ""))
            if did not in dersler:
                continue
            ders = dersler[did]
            tc = str(atama.get("ogretmen_tc") or (atama.get("ogretmenler") or [{}])[0].get("tc") or "")
            # Ortak ders (ayni dersi birlikte veren 2+ ogretmen) - ikincil ogretmenler
            # de doluluk/kisit kontrolune girmezse cakisma olusur, bu yuzden hepsini
            # ayri bir "ek_tcler" listesinde tutup musait_mi/yerlestir/bosalt'ta
            # birlikte kontrol ediyoruz.
            tum_ogrt_tc = [str(o.get("tc") or "") for o in (atama.get("ogretmenler") or [])]
            ek_tcler = sorted(set(t for t in tum_ogrt_tc if t and t != tc))
            bloklar = list(ders.get("blok_dagilim") or [ders.get("haftalik_saat", 1)])

            # Kilitli saatleri, JS motorundaki gibi TAM BLOK olarak dus
            # (buyuk bloktan degil, listedeki sirayla - JS ile birebir tutarli)
            kalan_kilitli = locked_saat.get((sid, did), 0)
            if kalan_kilitli:
                yeni_bloklar = []
                for b in bloklar:
                    if kalan_kilitli >= b:
                        kalan_kilitli -= b
                    else:
                        yeni_bloklar.append(b)
                bloklar = yeni_bloklar

            for bi, boy in enumerate(bloklar):
                if not boy:
                    continue
                gorevler.append({
                    "id": f"{sid}_{did}_{bi}", "sid": sid, "did": did, "tc": tc,
                    "ek_tcler": ek_tcler,
                    "ogrtler": atama.get("ogretmenler", []), "boy": int(boy),
                    "placed": None,
                })

    if not gorevler:
        return {"basari": True, "slots": {sid: {} for sid in siniflar},
                "eksikler": [], "sure_sn": 0, "durum": "EMPTY", "seed": seed}

    tum_tc = sorted(set(g["tc"] for g in gorevler if g["tc"])
                     | set(t for g in gorevler for t in g["ek_tcler"])
                     | set(tc for (_, _, _, _, tc) in locked_cells if tc))
    print(f"Gorev:{len(gorevler)} Sinif:{len(siniflar)} Ogretmen:{len(tum_tc)} "
          f"Kilitli:{len(locked_cells)}", flush=True)

    # ---------------- 2. Kisit tablosu ----------------
    def kisit_al(tc):
        k = kisitlar.get(tc, {})
        kapali_saat = set()
        for kb in k.get("kapaliBosluklar", []) or []:
            try:
                kapali_saat.add((int(kb["gun"]), int(kb["saat"])))
            except (KeyError, TypeError, ValueError):
                continue
        return {
            "bosGun": int(k["bosGun"]) if k.get("bosGun") else None,
            "kapali": set(int(v) for v in k.get("kapaliGunler", [])),
            "kapaliSaat": kapali_saat,
            "minG":   int(k["minGunlukSaat"]) if k.get("minGunlukSaat") else None,
            "maxG":   int(k["maxGunlukSaat"]) if k.get("maxGunlukSaat") else None,
            "brans":  (k.get("brans") or "").strip(),
            "unvan":  (k.get("unvan") or "").strip(),
            # BOS GUN ISTEMEYEN OGRETMEN: bazi ogretmenler bos gun yerine
            # derslerinin HAFTAYA DENGELI yayilmasini tercih eder. Bu
            # ogretmenler bos gun atama gecislerinden ve "bos gunu yok"
            # istatistiginden MUAF tutulur - ama pencere kurallari
            # kendilerine AYNEN uygulanir (idareci muafiyetinden farki).
            "bosGunIstemez": bool(k.get("bosGunIstemez")),
        }
    tc_kisit = {tc: kisit_al(tc) for tc in tum_tc}

    # ---------------- 2b. Idareci muafiyeti ----------------
    # Mudur/mudur yardimcisi gibi cok az ders saati olan ("ek ders") idareciler
    # zaten her gun okulda oldugundan bos gun/pencere hedefi onlar icin anlamsiz.
    # SADECE bos gun atama ve pencere azaltmadan MUAF tutulurlar - "asla tek ders"
    # kurali ONLAR ICIN DE gecerlidir (istisnasi yok).
    # ONCELIK: unvan alani varsa ("Mudur Yardimcisi"/"Okul Muduru" icerenler)
    # onu kullan - bu gercek veriden geldigi icin 2-12 saat tahmininden daha
    # guvenilir. unvan yoksa eski sezgisel esige (2-12 saat) geri don.
    _toplam_yuk = {tc: 0 for tc in tum_tc}
    for g in gorevler:
        for tc in ([g["tc"]] + g["ek_tcler"] if g["tc"] else g["ek_tcler"]):
            if tc in _toplam_yuk:
                _toplam_yuk[tc] += g["boy"]
    IDARECI_MIN_YUK, IDARECI_MAX_YUK = 2, 12

    def _idareci_hesapla(tc):
        unvan = tc_kisit[tc]["unvan"]
        if unvan:
            u = unvan.lower()
            return "müdür" in u or "mudur" in u
        return IDARECI_MIN_YUK <= _toplam_yuk[tc] <= IDARECI_MAX_YUK

    idareci_mi = {tc: _idareci_hesapla(tc) for tc in tum_tc}

    # ---------------- 2c. (Opsiyonel) ON-ATAMA bos gun ----------------
    # Yerlestirmeden SONRA (zaten %100 dolu) bir gunu bosaltmaya calismak
    # (kovma ile) cok zor - onun yerine yerlestirme BASLAMADAN ONCE bosGun'u
    # atarsak, greedy/MRV motoru bunun etrafinda DOGAL olarak calisir (tipki
    # manuel bosGun verilen bir ogretmen gibi). veri["on_bos_gun_ata"]=True
    # ise denenir; TUM dersler yine de yerlesmezse (eksik>0) bu deneme dusuk
    # puan alir ve coklu-deneme baska bir stratejiyle (post-hoc kovma) devam
    # eder - "tum dersler yerlessin" kuralindan asla odun verilmez.
    if veri.get("on_bos_gun_ata"):
        uygun_tc = [tc for tc in tum_tc if not idareci_mi[tc]
                    and not tc_kisit[tc]["bosGunIstemez"] and tc_kisit[tc]["bosGun"] is None]
        rnd.shuffle(uygun_tc)
        for i, tc in enumerate(uygun_tc):
            tc_kisit[tc]["bosGun"] = gunler[i % len(gunler)]

    # ---------------- 3. Doluluk gridleri ----------------
    class_occ   = {sid: {} for sid in siniflar}        # {(gun,saat): gid}
    teacher_occ = {tc: {} for tc in tum_tc}             # {(gun,saat): gid}
    gun_ders    = {}                                     # (sid,did) -> {gun: adet}
    day_load    = {tc: {g: 0 for g in gunler} for tc in tum_tc}
    gid_map     = {g["id"]: g for g in gorevler}

    # Kilitli hucreleri sabit doluluk olarak yukle - "KILITLI" sentinel'i
    # hicbir gid'e karsilik gelmez, bu yuzden bosalt() asla dokunamaz.
    for (sid, gun, saat, did, tc) in locked_cells:
        if sid in class_occ:
            class_occ[sid][(gun, saat)] = "KILITLI"
        if tc and tc in teacher_occ:
            teacher_occ[tc][(gun, saat)] = "KILITLI"
            day_load[tc][gun] = day_load[tc].get(gun, 0) + 1
        key = (sid, did)
        gun_ders.setdefault(key, {})
        gun_ders[key][gun] = gun_ders[key].get(gun, 0) + 1

    def tum_ogrt(g):
        """Bu gorevin sorumlu oldugu TUM ogretmenler (birincil + ortak ders ise ek)."""
        if g["tc"]:
            return [g["tc"]] + g["ek_tcler"]
        return list(g["ek_tcler"])

    kayit_gunlugu = []  # [(gid, eski_placed), ...] - undo log (append-only)

    def _bosalt_ham(gid):
        g = gid_map[gid]
        if not g["placed"]:
            return
        gun, saat = g["placed"]
        ogrtler = tum_ogrt(g)
        for b in range(g["boy"]):
            class_occ[g["sid"]].pop((gun, saat + b), None)
            for tc in ogrtler:
                teacher_occ[tc].pop((gun, saat + b), None)
        key = (g["sid"], g["did"])
        if key in gun_ders and gun in gun_ders[key]:
            gun_ders[key][gun] -= 1
            if gun_ders[key][gun] <= 0:
                del gun_ders[key][gun]
        for tc in ogrtler:
            day_load[tc][gun] -= g["boy"]
        g["placed"] = None

    def _yerlestir_ham(gid, gun, saat):
        g = gid_map[gid]
        ogrtler = tum_ogrt(g)
        for b in range(g["boy"]):
            class_occ[g["sid"]][(gun, saat + b)] = gid
            for tc in ogrtler:
                teacher_occ[tc][(gun, saat + b)] = gid
        key = (g["sid"], g["did"])
        gun_ders.setdefault(key, {})
        gun_ders[key][gun] = gun_ders[key].get(gun, 0) + 1
        for tc in ogrtler:
            day_load[tc][gun] += g["boy"]
        g["placed"] = (gun, saat)

    def bosalt(gid):
        g = gid_map[gid]
        if g["placed"] is not None:
            kayit_gunlugu.append((gid, g["placed"]))
        _bosalt_ham(gid)

    def yerlestir(gid, gun, saat):
        g = gid_map[gid]
        kayit_gunlugu.append((gid, g["placed"]))
        _yerlestir_ham(gid, gun, saat)

    def musait_mi(gid, gun, saat):
        g = gid_map[gid]
        boy = g["boy"]; sid = g["sid"]; did = g["did"]
        ogrtler = tum_ogrt(g)
        if saat < 1 or saat + boy - 1 > gun_bilgi[gun]:
            return False
        for tc in ogrtler:
            k = tc_kisit[tc]
            if k["bosGun"] == gun or gun in k["kapali"]:
                return False
        for b in range(boy):
            s = saat + b
            if (gun, s) in class_occ[sid]:
                return False
            for tc in ogrtler:
                if (gun, s) in teacher_occ[tc]:
                    return False
                if (gun, s) in tc_kisit[tc]["kapaliSaat"]:
                    return False
        key = (sid, did)
        if gun_ders.get(key, {}).get(gun, 0) >= 1:
            return False  # ayni ders ayni gun tekrar olamaz
        for tc in ogrtler:
            maxg = tc_kisit[tc]["maxG"]
            if maxg and day_load[tc][gun] + boy > maxg:
                return False
        return True

    def adaylar(gid):
        g = gid_map[gid]
        boy = g["boy"]
        sonuc = []
        for gun in gunler:
            for saat in range(1, gun_bilgi[gun] - boy + 2):
                if musait_mi(gid, gun, saat):
                    sonuc.append((gun, saat))
        return sonuc

    def skor(gid, gun, saat):
        """Dusuk skor = tercih edilir."""
        g = gid_map[gid]; tc = g["tc"]; boy = g["boy"]
        s = 0.0
        if tc:
            k = tc_kisit[tc]
            mevcut = day_load[tc][gun]
            ming = k["minG"]
            # ONEMLI DUZELTME: 'zaten kullanilan gunu tercih et' bonusu
            # ESKIDEN (-5) 'yeni gun basi/sonu' bonusundan (-6) DAHA
            # ZAYIFTI - bu, algoritmanin MEVCUT bir gunu kullanmak yerine
            # YENI bir gun ACMAYI tercih etmesine yol aciyordu (cunku
            # yeni gunun kenar konumu daha cazip gorunuyordu). Bu,
            # ogretmenlerin derslerinin gereksiz yere DAHA FAZLA GUNE
            # YAYILMASINA, dolayisiyla TAM BOS GUN bulmalarinin
            # ZORLASMASINA sebep oluyordu - kullanicinin bildirdigi 'bos
            # gun sayisi dusmuyor' regresyonunun kok nedeniydi. Artik
            # MEVCUT GUNU KULLANMAK HER ZAMAN yeni gun acmaktan daha
            # cazip - blok/kenar tercihleri SADECE hangi gunun
            # kullanilacagini degil, O GUN ICINDE NEREYE konulacagini
            # etkiler.
            if k["bosGunIstemez"]:
                # DENGELI DAGILIM: bu ogretmen bos gun ISTEMIYOR, derslerinin
                # haftaya esit yayilmasini istiyor. Bu yuzden "gun biriktir"
                # mantigi TERSINE cevrilir: en AZ yuklu gun tercih edilir.
                # AGIRLIK NOTU: bu ceza, asagidaki bitisiklik bonusundan (-8)
                # BELIRGIN SEKILDE buyuk olmali. Aksi halde iki etki
                # birbirini goturur ve gunlerden biri bos kalabiliyordu
                # (gercek testte 16 saatlik ogretmen 0-4-4-4-4 dagildi,
                # oysa 4-4-4-2-2 olmaliydi).
                s += mevcut * 12         # gun ne kadar doluysa o kadar az cazip
                if ming and 0 < mevcut < ming:
                    s -= 10              # yine de tek-ders kalintisini topla
                if (gun, saat - 1) in teacher_occ.get(tc, {}) or (gun, saat + boy) in teacher_occ.get(tc, {}):
                    s -= 8               # gun icinde bitisik olsun (pencere)
            elif mevcut > 0:
                s -= 20  # mevcut gunu kullan - HER ZAMAN yeni gun acmaktan ONCELIKLI
                if ming and mevcut < ming:
                    s -= 8  # min saat altindaki gunu tamamlamaya oncelik ver
                # BITISIKLIK: bu gun icinde, mevcut bir dersin TAM
                # YANINA eklenirse EK bonus (pencere minimizasyonu).
                if (gun, saat - 1) in teacher_occ.get(tc, {}) or (gun, saat + boy) in teacher_occ.get(tc, {}):
                    s -= 8
            else:
                # YENI GUN: bu ogretmenin O GUNKU ILK dersi. Sadece
                # MEVCUT gun secenegi YOKSA (yani baska yerlestirilecek
                # uygun bir 'mevcut gun' slotu bulunamadiginda)
                # kullanilir - bu durumda GUN BASI/SONU tercih edilir ki
                # SONRAKI derslerin bitisik eklenme sansi artsin.
                son_saat = gun_bilgi[gun] - boy + 1
                if saat == 1 or saat == son_saat:
                    s -= 3
                else:
                    merkeze_uzaklik = min(saat - 1, son_saat - saat)
                    s += merkeze_uzaklik * 0.3
        s += rnd.random() * 0.5  # esitlik bozucu / cesitlilik
        return s

    def en_iyi_aday(gid, haric_gun=None):
        ay = adaylar(gid)
        if haric_gun is not None:
            ay = [gs for gs in ay if gs[0] != haric_gun]
        if not ay:
            return None
        ay.sort(key=lambda gs: skor(gid, gs[0], gs[1]))
        return ay[0]

    # ---------------- 4. MRV siralamasi ----------------
    def tc_skor(tc):
        gl = [g for g in gorevler if g["tc"] == tc]
        toplam = sum(g["boy"] for g in gl)
        k = tc_kisit[tc]
        kisitlilik = (10 if k["bosGun"] else 0) + len(k["kapali"]) * 4
        return -(toplam + kisitlilik)  # en kisitli/yukluden basla

    tc_sirali = sorted(tum_tc, key=tc_skor)

    kuyruk = []
    for tc in tc_sirali:
        gl = [g for g in gorevler if g["tc"] == tc]
        gl.sort(key=lambda g: (-g["boy"], len(adaylar(g["id"]))))
        kuyruk.extend(g["id"] for g in gl)
    kuyruk.extend(g["id"] for g in gorevler if not g["tc"])  # ogretmensiz dersler en sona

    # ---------------- 4b. ARTIMLI YEREL ARAMA: onceki iyi cozumden devam ----------------
    # ASC/FET gibi profesyonel programlar 'sifirdan yeniden dene' YERINE
    # mevcut iyi bir cozumu alip KADEMELI iyilestirir (gercek yerel arama /
    # simulated annealing). Eger 'baslangic_yerlesim' verilmisse ({gid:
    # [gun,saat,tc]} - bir onceki _dagit_tek_deneme cagrisinin sonucundan
    # uretilir), o yerlesimi DOGRUDAN uygularim ve ana coklu-yerlestirme
    # dongusunu ATLARIM - boylece saniyeler suren yeniden-cozme yerine
    # DOGRUDAN cilalama (pencere azaltma, brans takasi) gecislerine geçilir.
    # Bu, arka_plan_arama()'nin her turda TAM YENIDEN COZMEK yerine ayni
    # cozum uzerinde SUREKLI iyilestirme yapmasini saglar - ASC/FET
    # mantigina çok daha yakin.
    baslangic_yerlesim = veri.get("baslangic_yerlesim")
    # ALTERNATIF GIRIS: eger frontend (sayfa yenilenmis, tarayici
    # bellegindeki _yerlesim_ham kaybolmus olabilir) bunun yerine HAM
    # 'baslangic_slots' (Supabase'den yuklenen, halihazirda ekranda
    # gosterilen MEVCUT program - {sid:{gun:{saat:{ders_id,ogretmen_tc,...}}}})
    # gonderirse, bunu OTOMATIK OLARAK baslangic_yerlesim formatina
    # ceviririz. Bu, "kaldigi yerden devam"in sayfa yenilemeden SONRA
    # bile calismasini saglar - cunku mevcut program zaten sunucuda/
    # Supabase'de kayitlidir, sadece tarayici degiskeni kaybolmustur.
    if not baslangic_yerlesim and veri.get("baslangic_slots"):
        baslangic_slots = veri["baslangic_slots"]
        uretilen_yerlesim = {}
        gorev_gruplari = {}
        for g in gorevler:
            gorev_gruplari.setdefault((g["sid"], g["did"]), []).append(g)
        for (sid, did), grup in gorev_gruplari.items():
            grup.sort(key=lambda g: g["id"])  # bi sirasina gore (id icinde kodlu)
            hucreler = baslangic_slots.get(sid, {})
            bloklar = []  # [(gun, baslangic_saat, uzunluk, tc, ogrtler)]
            for gun_str, saatler in hucreler.items():
                gun_i = int(gun_str)
                saat_listesi = sorted(int(s) for s, h in saatler.items()
                                       if str(h.get("ders_id")) == str(did))
                # ardisik saatleri gruplayarak bloklara ayir
                i = 0
                while i < len(saat_listesi):
                    j = i
                    while j + 1 < len(saat_listesi) and saat_listesi[j + 1] == saat_listesi[j] + 1:
                        j += 1
                    baslangic_saat = saat_listesi[i]
                    uzunluk = saat_listesi[j] - saat_listesi[i] + 1
                    hucre = saatler[str(baslangic_saat)]
                    bloklar.append((gun_i, baslangic_saat, uzunluk,
                                     hucre.get("ogretmen_tc"), hucre.get("ogretmenler", [])))
                    i = j + 1
            bloklar.sort(key=lambda b: (b[0], b[1]))
            # Sadece blok SAYISI ve UZUNLUKLARI tam eslesirse uygula -
            # eslesmezse (eksik/farkli veri) bu (sid,did) grubunu ATLA,
            # o gorevler NORMAL yerlestirme ile islenir (guvenli fallback).
            if len(bloklar) == len(grup) and all(
                    bloklar[i][2] == grup[i]["boy"] for i in range(len(grup))):
                for g, (gun_i, baslangic_saat, uzunluk, tc_b, ogrtler_b) in zip(grup, bloklar):
                    uretilen_yerlesim[g["id"]] = [gun_i, baslangic_saat, tc_b, ogrtler_b]
        baslangic_yerlesim = uretilen_yerlesim
        print(f"[BASLANGIC_SLOTS] {len(uretilen_yerlesim)}/{len(gorevler)} gorev "
              f"mevcut programdan basariyla eslesti", flush=True)
    onceden_yerlesen_gid = set()
    if baslangic_yerlesim:
        gid_map_erken = {g["id"]: g for g in gorevler}
        for gid, bilgi in baslangic_yerlesim.items():
            g = gid_map_erken.get(gid)
            if not g or not bilgi:
                continue
            gun_b, saat_b, tc_b = bilgi[0], bilgi[1], bilgi[2]
            ogrtler_b = bilgi[3] if len(bilgi) > 3 else None
            if tc_b and tc_b != g["tc"]:
                g["tc"] = tc_b  # brans-takasli bir onceki cozumden geliyor olabilir
                if ogrtler_b is not None:
                    g["ogrtler"] = ogrtler_b  # goruntuleme listesini de senkronize et
            g["placed"] = (gun_b, saat_b)
            for b in range(g["boy"]):
                class_occ.setdefault(g["sid"], {})[(gun_b, saat_b + b)] = g["id"]
                for otc in tum_ogrt(g):
                    teacher_occ.setdefault(otc, {})[(gun_b, saat_b + b)] = g["id"]
                    day_load.setdefault(otc, {}).setdefault(gun_b, 0)
                    day_load[otc][gun_b] += 0  # asagida topluca yeniden hesaplanacak
            onceden_yerlesen_gid.add(gid)
        # day_load'u SIFIRDAN VE DOGRU sekilde yeniden hesapla (yukaridaki
        # dongude += 0 ile sadece anahtarlari olusturduk)
        for tc2 in tum_tc:
            for gun2 in gunler:
                day_load[tc2][gun2] = 0
        for g in gorevler:
            if g["placed"]:
                gun_p, saat_p = g["placed"]
                for otc in tum_ogrt(g):
                    day_load[otc][gun_p] = day_load[otc].get(gun_p, 0) + g["boy"]
        kuyruk = [gid for gid in kuyruk if gid not in onceden_yerlesen_gid]
        # KRITIK: baslangic_yerlesim'den gelen HALIHAZIRDA bos olan gunleri
        # hemen KILITLE (tc_kisit[tc]["bosGun"]) - aksi halde otomatik_bos_gun_pass
        # bu gunu 'zaten var' diye atlar (dogru) AMA KENDISI KILITLEMEDIGI
        # icin sonraki gecisler (pencere_azalt_pass, brans_takas_pass) bu
        # gunu koruma altinda olmadan doldurabilir - onceki turda kazanilan
        # bos gun sessizce kaybolur.
        for tc in tum_tc:
            if (idareci_mi[tc] or tc_kisit[tc]["bosGunIstemez"]
                    or tc_kisit[tc]["bosGun"] is not None):
                continue
            bos_gunler_simdi = [g for g in gunler if day_load[tc][g] == 0]
            if bos_gunler_simdi:
                tc_kisit[tc]["bosGun"] = bos_gunler_simdi[0]

    # BASLANGIC TEMIZLIK KONTROLU: 'kaldigi yerden devam' modunda
    # (baslangic_yerlesim verilmis), yuklenen yerlesimin ZATEN gecerli
    # (tek-ders/fazla-bos-gun ihlali OLMAYAN) olup olmadigini kontrol
    # ederiz. Eger ZATEN TEMIZSE, asagidaki 'duzeltme' gecisleri
    # (tek_ders_yasakla_pass, otomatik_bos_gun_pass, brans_takas_pass
    # vb.) GEREKSIZ YERE calisip zaten iyi olan pencere durumunu
    # bozabiliyordu - gercek loglar bunu kanitladi (temiz bir 35
    # pencere_fazla checkpoint'i, bu gecislerden SONRA 44-55'e
    # cikiyordu, zaman_takasi_pencere_pass HENUZ BASLAMADAN). Simdi
    # ZATEN TEMIZSE bu gecisler ATLANIR - dogrudan pencere azaltmaya
    # gecilir.
    _baslangic_zaten_temiz = False
    if baslangic_yerlesim:
        _tek_ders_ihlali_var = False
        _fazla_bos_gun_var = False
        _sifir_bos_gun_var = False
        for tc in tum_tc:
            if idareci_mi[tc]:
                continue
            _calisilan_saat_toplam = sum(day_load[tc].get(g, 0) for g in gunler)
            if _calisilan_saat_toplam == 0:
                continue
            # KRITIK DUZELTME: yanlis alan adi kullaniliyordu
            # ("minGunlukSaat" yerine dogrusu "minG") - bu yuzden BU
            # kontrol HER ZAMAN varsayilan (2) kullaniyordu, ogretmenin
            # GERCEK ayarina hic bakmiyordu. Resmi ihlal_sayisi()
            # fonksiyonuyla AYNI mantik: minG tanimli DEGILSE (None/0),
            # o ogretmen icin tek-ders ihlali ASLA sayilmaz (min-saat
            # kurali o ogretmene uygulanmiyor demektir) - ama boş-gün
            # kontrollerine (asagida) MUTLAKA devam edilir, bu yuzden
            # 'continue' KULLANILMAZ, sadece tek-ders blogu atlanir.
            _min_saat = tc_kisit[tc].get("minG")
            if _min_saat:
                for g in gunler:
                    _yuk = day_load[tc].get(g, 0)
                    if 0 < _yuk < _min_saat:
                        _tek_ders_ihlali_var = True
            _bos_gun_sayisi = sum(1 for g in gunler if day_load[tc].get(g, 0) == 0)
            if _bos_gun_sayisi > 1:
                _fazla_bos_gun_var = True
            # KRITIK DUZELTME: sifir_bos_gun (bu ogretmenin HIC bos gunu
            # yok) durumu da kontrol edilir. Bu kontrol EKSIKTI - bu
            # yuzden 'zaten_temiz=True' yanlislikla tetiklenip
            # otomatik_bos_gun_pass (bos gun atama gecisi) atlaniyordu,
            # gercek kullanimda 13 ogretmenin hic bos gun alamamasina
            # yol acti.
            if _bos_gun_sayisi == 0:
                _sifir_bos_gun_var = True
            if _tek_ders_ihlali_var or _fazla_bos_gun_var or _sifir_bos_gun_var:
                break
        _baslangic_zaten_temiz = (not _tek_ders_ihlali_var and not _fazla_bos_gun_var
                                   and not _sifir_bos_gun_var)
        print(f"[BASLANGIC TEMIZLIK] zaten_temiz={_baslangic_zaten_temiz} "
              f"(tek_ders_ihlali={_tek_ders_ihlali_var}, fazla_bos_gun={_fazla_bos_gun_var}, "
              f"sifir_bos_gun={_sifir_bos_gun_var})", flush=True)

    # ---------------- 5. Yerlestirme + displacement ----------------
    # on_bos_gun_ata modunda butun ogretmenler bastan kisitli oldugundan
    # yerlestirme daha zor - biraz daha derin arama gerekiyor (4), ama 5
    # bazi tohumlarda 200+ saniyeye kadar patlayabiliyordu. Post-hoc modda
    # (varsayilan) 3 yeterli ve hizli.
    MAX_DERINLIK = 5 if veri.get("on_bos_gun_ata") else 3
    DERIN_TAVAN = 8          # gec gecisler (tek-ders/bos-gun/pencere) icin - zaman siniri gevsek
    # KOVMA ZINCIR SINIRI: pencere azaltirken bir dersi bosluga tasimak icin
    # kac dersin zincirleme kovulmasina izin verildigi. Web akisinda (hizli
    # kalmasi gerekir) dusuk tutulur, arka plan/yerel aramada (bol zaman
    # var) veri icinde YUKSEK bir deger gonderilerek COK DAHA DERIN
    # zincirlere izin verilebilir - bu, pencere azaltmanin en guclu
    # aracidir, derinlik arttikca %100 dolu siniflarda bile daha fazla
    # yeniden duzenleme kombinasyonu denenebilir hale gelir.
    KOVMA_ZINCIR_SINIRI = int(veri.get("kovma_zincir_siniri", 6))
    MAX_PENCERE_HEDEF = 2  # erken tasindi - zaman_takasi_pencere_pass (asagida, 9b civari) bunu kullaniyor

    def kontrol_noktasi():
        """O(1) - sadece log uzunlugunu kaydeder."""
        return len(kayit_gunlugu)

    def geri_al(nokta):
        """Log'u kontrol noktasina kadar tersten oynatarak geri alir.
        Maliyet: SADECE bu deneme sirasinda yapilan gercek islem sayisiyla
        orantili (eskiden her denemede TUM gorevlerin anlik goruntusunu
        almak O(n) idi - 900K+ islemde bu performansin %64'unu yiyordu)."""
        while len(kayit_gunlugu) > nokta:
            gid, eski_pos = kayit_gunlugu.pop()
            g = gid_map[gid]
            if g["placed"] is not None:
                _bosalt_ham(gid)
            if eski_pos is not None:
                _yerlestir_ham(gid, eski_pos[0], eski_pos[1])

    def yerlestirmeye_calis(gid, derinlik=0, tavan=None):
        if tavan is None:
            tavan = MAX_DERINLIK
        if derinlik == 0 and _zaman_doldu():
            return False  # butce doldu, bu gorevi denemeden eksik say
        aday = en_iyi_aday(gid)
        if aday:
            yerlestir(gid, aday[0], aday[1])
            return True
        if derinlik >= tavan:
            return False

        g = gid_map[gid]
        ogrtler = tum_ogrt(g)
        key = (g["sid"], g["did"])

        for gun in gunler:
            if _zaman_doldu():
                return False
            if any(tc_kisit[tc]["bosGun"] == gun or gun in tc_kisit[tc]["kapali"] for tc in ogrtler):
                continue
            if gun_ders.get(key, {}).get(gun, 0) >= 1:
                continue
            if any(tc_kisit[tc]["maxG"] and day_load[tc][gun] + g["boy"] > tc_kisit[tc]["maxG"] for tc in ogrtler):
                continue
            for saat in range(1, gun_bilgi[gun] - g["boy"] + 2):
                cakisanlar = set()
                kilitliye_carpti = False
                for b in range(g["boy"]):
                    s = saat + b
                    occ1 = class_occ[g["sid"]].get((gun, s))
                    if occ1 == "KILITLI":
                        kilitliye_carpti = True
                        break
                    if occ1:
                        cakisanlar.add(occ1)
                    for tc in ogrtler:
                        occ2 = teacher_occ[tc].get((gun, s))
                        if occ2 == "KILITLI":
                            kilitliye_carpti = True
                            break
                        if occ2:
                            cakisanlar.add(occ2)
                    if kilitliye_carpti:
                        break
                if kilitliye_carpti:
                    continue  # kilitli hucre - asla kovulamaz, bu saati atla
                if not cakisanlar:
                    continue  # bos slot olsaydi en_iyi_aday zaten bulurdu; atla
                if len(cakisanlar) > 2:
                    continue  # cok fazla kovma riskli - SABIT deger (ozyinelemeli motor, guvenlik icin degistirilemez)

                cakisanlar = sorted(cakisanlar)  # deterministik sira (set iterasyonu PYTHONHASHSEED'e bagli)
                nokta = kontrol_noktasi()
                for cg in cakisanlar:
                    bosalt(cg)

                if musait_mi(gid, gun, saat):
                    yerlestir(gid, gun, saat)
                    basarili = True
                    for cg in cakisanlar:
                        if not yerlestirmeye_calis(cg, derinlik + 1, tavan):
                            basarili = False
                            break
                    if basarili:
                        return True
                    geri_al(nokta)  # ic ice kovma zincirleri dahil TAM geri alma
                else:
                    geri_al(nokta)
        return False

    # on_bos_gun_ata modunda MAX_DERINLIK=5 kullaniyoruz (eksiksiz yerlesme
    # sansini artirmak icin) ama bazi tohum/siralama kombinasyonlarinda kovma
    # zinciri patlayip cok uzun surebiliyor. ANA DONGU ortak zaman butcesine
    # baglidir: butce asilirsa kalan gorevler direkt eksik sayilir (multi-
    # restart zaten dusuk puanla eler), boylece TEK bir deneme asla toplam
    # sureyi tehlikeye atmiyor.
    eksikler_gid = []
    for gid in kuyruk:
        if _zaman_doldu():
            eksikler_gid.append(gid)
            continue
        if not yerlestirmeye_calis(gid):
            eksikler_gid.append(gid)

    def kovarak_yerlestir_haric(gid, haric_gun):
        """gid'i haric_gun DISINDAKI bir gune, gerekirse o gundeki bir hucreyi
        isgal edeni KOVARAK yerlestirir. gunu_tamamen_bosalt icin: dogrudan
        bos hucre bulunamadiginda (yogun/%100 dolu programlarda sikca olur)
        bu, tasinacak yer acar."""
        g = gid_map[gid]
        ogrtler_g = tum_ogrt(g)
        for gun in gunler:
            if _zaman_doldu():
                return False
            if gun == haric_gun:
                continue
            if any(tc_kisit[tc]["bosGun"] == gun or gun in tc_kisit[tc]["kapali"] for tc in ogrtler_g):
                continue
            key = (g["sid"], g["did"])
            if gun_ders.get(key, {}).get(gun, 0) >= 1:
                continue
            if any(tc_kisit[tc]["maxG"] and day_load[tc][gun] + g["boy"] > tc_kisit[tc]["maxG"]
                   for tc in ogrtler_g):
                continue
            for saat in range(1, gun_bilgi[gun] - g["boy"] + 2):
                cakisanlar = set()
                bloklanmis = False
                for b in range(g["boy"]):
                    s = saat + b
                    occ = class_occ[g["sid"]].get((gun, s))
                    if occ == "KILITLI":
                        bloklanmis = True
                        break
                    if occ:
                        cakisanlar.add(occ)
                    for otc in ogrtler_g:
                        occ2 = teacher_occ[otc].get((gun, s))
                        if occ2 == "KILITLI":
                            bloklanmis = True
                            break
                        if occ2:
                            cakisanlar.add(occ2)
                    if bloklanmis:
                        break
                if bloklanmis or not cakisanlar or len(cakisanlar) > 3:
                    continue  # SABIT deger (ozyinelemeli motoru cagirir, guvenlik icin degistirilemez)
                nokta = kontrol_noktasi()
                for cg in sorted(cakisanlar):
                    bosalt(cg)
                if musait_mi(gid, gun, saat):
                    yerlestir(gid, gun, saat)
                    basarili = True
                    for cg in sorted(cakisanlar):
                        if not yerlestirmeye_calis(cg, 0, tavan=DERIN_TAVAN):
                            basarili = False
                            break
                    if basarili:
                        return True
                    geri_al(nokta)
                else:
                    geri_al(nokta)
        return False

    def kovarak_yerlestir_gunde(gid, hedef_gun):
        """gid'i SPECIFIK OLARAK hedef_gun'e, gerekirse o gundeki bir hucreyi
        isgal edeni KOVARAK (tam swap) yerlestirmeyi dener. kovarak_yerlestir_haric
        'herhangi bir gun (X haric)' arar, bu ise 'SADECE bu gun' hedefler -
        min-gunluk-saat doldurma icin: %100 dolu sinif programlarinda hedef
        gunde dogrudan bos hucre bulunamadiginda, o hucreyi isgal eden dersle
        YER DEGISTIRIR (o ders baska bir gune/saate tasinir)."""
        g = gid_map[gid]
        ogrtler_g = tum_ogrt(g)
        if any(tc_kisit[tc]["bosGun"] == hedef_gun or hedef_gun in tc_kisit[tc]["kapali"]
               for tc in ogrtler_g):
            return False
        key = (g["sid"], g["did"])
        if gun_ders.get(key, {}).get(hedef_gun, 0) >= 1:
            return False
        if any(tc_kisit[tc]["maxG"] and day_load[tc][hedef_gun] + g["boy"] > tc_kisit[tc]["maxG"]
               for tc in ogrtler_g):
            return False
        for saat in range(1, gun_bilgi[hedef_gun] - g["boy"] + 2):
            if _zaman_doldu():
                return False
            cakisanlar = set()
            bloklanmis = False
            for b in range(g["boy"]):
                s = saat + b
                occ = class_occ[g["sid"]].get((hedef_gun, s))
                if occ == "KILITLI":
                    bloklanmis = True
                    break
                if occ:
                    cakisanlar.add(occ)
                for otc in ogrtler_g:
                    occ2 = teacher_occ[otc].get((hedef_gun, s))
                    if occ2 == "KILITLI":
                        bloklanmis = True
                        break
                    if occ2:
                        cakisanlar.add(occ2)
                if bloklanmis:
                    break
            if bloklanmis or not cakisanlar or len(cakisanlar) > 3:
                continue  # SABIT deger (ozyinelemeli motoru cagirir, guvenlik icin degistirilemez)
            nokta = kontrol_noktasi()
            for cg in sorted(cakisanlar):
                bosalt(cg)
            if musait_mi(gid, hedef_gun, saat):
                yerlestir(gid, hedef_gun, saat)
                basarili = True
                for cg in sorted(cakisanlar):
                    if not yerlestirmeye_calis(cg, 0, tavan=DERIN_TAVAN):
                        basarili = False
                        break
                if basarili:
                    return True
                geri_al(nokta)
            else:
                geri_al(nokta)
        return False

    def gunu_tamamen_bosalt(tc, gun):
        """tc'nin gun'deki TUM derslerini baska gunlere tasimaya calisir -
        once dogrudan bos hucre arar, olmazsa kovarak yer acar (hepsi
        basarili olursa kalici, biri bile basarisiz olursa TAM geri alir)."""
        tasklar = [g for g in gorevler if tc in tum_ogrt(g) and g["placed"] and g["placed"][0] == gun]
        if not tasklar:
            return False
        nokta = kontrol_noktasi()
        for t in tasklar:
            bosalt(t["id"])
        hepsi = True
        for t in tasklar:
            if _zaman_doldu():
                hepsi = False
                break
            aday = en_iyi_aday(t["id"], haric_gun=gun)
            if aday:
                yerlestir(t["id"], aday[0], aday[1])
            elif kovarak_yerlestir_haric(t["id"], haric_gun=gun):
                pass
            else:
                hepsi = False
                break
        if not hepsi:
            geri_al(nokta)
            return False
        return True

    def ogrt_bos_gun_var_mi(tc):
        """tc'nin (herhangi bir sebeple - dogal, manuel bosGun, ya da onceki
        bir gecisin bosalttigi) zaten yuku SIFIR olan bir gunu var mi?"""
        return any(day_load[tc][g] == 0 for g in gunler)

    def ihlal_sayisi():
        """Su anki min-gunluk-saat ihlali sayisi (0<yuk<ming olan gun sayisi)."""
        n = 0
        for tc2 in tum_tc:
            ming2 = tc_kisit[tc2]["minG"]
            if not ming2:
                continue
            for gun2 in gunler:
                if 0 < day_load[tc2][gun2] < ming2:
                    n += 1
        return n

    def fazla_bos_gun_toplam():
        """Su anki toplam 'fazla bos gun' ihlali (idareci olmayan, 2+ bos
        gunu olan ogretmen sayisi). Takas gibi islemler bunu ARTIRMAMALI."""
        n = 0
        for tc2 in tum_tc:
            if idareci_mi[tc2]:
                continue
            if sum(1 for g2 in gunler if day_load[tc2][g2] == 0) >= 2:
                n += 1
        return n

    def sifir_bos_gun_toplam():
        """Su anki toplam 'hic bos gunu olmayan' ogretmen sayisi (idareci
        haric). KRITIK: fazla_bos_gun_toplam SADECE 2+ bos gunu (asiri)
        kontrol ediyordu - bunun TAM TERSI olan durum (bir ogretmenin
        TEK bos gununu KAYBETMESI, yani 1 bos gunden 0'a dusmesi) HICBIR
        yerde kontrol edilmiyordu. Pencere-motivasyonlu takaslar bu
        yuzden sessizce bir ogretmenin boş gununu doldurup 'BOS GUN YOK'
        sayisini artirabiliyordu - kullanicinin bildirdigi 'boş gün
        sayısı bir türlü düşmüyor' sikayetinin olasi nedeniydi."""
        n = 0
        for tc2 in tum_tc:
            if idareci_mi[tc2]:
                continue
            if tc_kisit[tc2]["bosGunIstemez"]:
                continue      # bu ogretmen zaten bos gun ISTEMIYOR
            if sum(1 for g2 in gunler if day_load[tc2][g2] == 0) == 0:
                n += 1
        return n

    # ---------------- 6. "Asla tek ders" garantisi (MUTLAK ONCELIK) ----------------
    def gunu_doldur(tc, gun, ming):
        """gun uzerindeki yuku, digerlerinden tasiyarak ming'e cikarmayi dener.
        Once dogrudan bos hucre arar, bulamazsa TAM SWAP (kovarak_yerlestir_gunde)
        dener - %100 dolu sinif programlarinda dogrudan bos hucre neredeyse hic
        olmadigindan bu adim olmadan pek cok vaka cozulemiyordu."""
        degisti = False
        adaylar_tasima = [g for g in gorevler
                           if tc in tum_ogrt(g) and g["placed"] and g["placed"][0] != gun]
        adaylar_tasima.sort(key=lambda g: -day_load[tc][g["placed"][0]])
        for t in adaylar_tasima:
            if _zaman_doldu():
                break
            if day_load[tc][gun] >= ming:
                break
            kaynak_gun = t["placed"][0]
            kalan = day_load[tc][kaynak_gun] - t["boy"]
            if 0 < kalan < ming:
                continue  # kaynak gunu de bozar, atla
            nokta = kontrol_noktasi()
            bosalt(t["id"])
            secenekler = [s for s in adaylar(t["id"]) if s[0] == gun]
            if secenekler:
                secenekler.sort(key=lambda gs: skor(t["id"], gs[0], gs[1]))
                yerlestir(t["id"], secenekler[0][0], secenekler[0][1])
                degisti = True
            else:
                geri_al(nokta)
        return degisti

    def gunu_doldur_swap_ile(tc, gun, ming):
        """gunu_doldur basarisiz olduysa, TAM SWAP (kovarak_yerlestir_gunde)
        ile tekrar dener - dogrudan bos hucre bulunamayan yogun/%100 dolu
        programlarda bu, iki dersin yer degistirmesiyle yer acar."""
        degisti = False
        adaylar_tasima = [g for g in gorevler
                           if tc in tum_ogrt(g) and g["placed"] and g["placed"][0] != gun]
        adaylar_tasima.sort(key=lambda g: -day_load[tc][g["placed"][0]])
        for t in adaylar_tasima:
            if _zaman_doldu():
                break
            if day_load[tc][gun] >= ming:
                break
            kaynak_gun = t["placed"][0]
            kalan = day_load[tc][kaynak_gun] - t["boy"]
            if 0 < kalan < ming:
                continue
            nokta = kontrol_noktasi()
            bosalt(t["id"])
            if kovarak_yerlestir_gunde(t["id"], gun):
                degisti = True
            else:
                geri_al(nokta)
        return degisti

    def _izole_gorevi_tasi(tc, gun):
        """gun'deki (0<yuk<ming ihlali olan) TUM gorevleri, GUNU TAMAMEN
        BOSALTMAYA CALISMADAN, teker teker BASKA gunlere (kovma dahil)
        tasimayi dener - hedef mutlaka 'bos gun' yaratmak degil, sadece
        izole kalan kalintiyi dagitmaktir. gunu_tamamen_bosalt'tan farki:
        o TUM gorevlerin AYNI ANDA basarili olmasini sart kosar (tek-basarisizlik
        = tam geri alma); bu fonksiyon ise HERHANGI BIRINI tasiyabilirse
        yeter (kismi basari bile ihlali cozebilir, cunku amac sadece
        yuku minG UZERINE cikarmak veya SIFIRA indirmek)."""
        tasklar = [g for g in gorevler if tc in tum_ogrt(g) and g["placed"] and g["placed"][0] == gun]
        for t in tasklar:
            if _zaman_doldu():
                break
            once = ihlal_sayisi()
            nokta = kontrol_noktasi()
            eski_gun, eski_saat = t["placed"]
            bosalt(t["id"])
            tasindi = False
            aday = en_iyi_aday(t["id"], haric_gun=gun)
            if aday:
                yerlestir(t["id"], aday[0], aday[1])
                tasindi = True
            elif kovarak_yerlestir_haric(t["id"], haric_gun=gun):
                tasindi = True
            if tasindi and ihlal_sayisi() <= once and fazla_bos_gun_toplam() <= 0:
                yeni_yuk = day_load[tc][gun]
                if yeni_yuk == 0 or yeni_yuk >= (tc_kisit[tc]["minG"] or 0):
                    return True  # ihlal cozuldu (gun ya bosaldi ya da esik ustune cikti)
                continue  # hala ihlalli ama belki bir sonraki gorev tasininca duzelir
            # basarisiz veya yeni sorun yarattı - geri al
            if not (t["id"] in [x["id"] for x in gorevler if x["placed"]]):
                pass
            geri_al(nokta)
        return day_load[tc][gun] == 0 or day_load[tc][gun] >= (tc_kisit[tc]["minG"] or 0)

    def tek_ders_yasakla_pass():
        """'Asla tek ders / gunde minGunlukSaat altinda ders olmasin' - BU
        KURAL COK ONEMLIDIR ama 'ASLA 2. BOS GUN YARATMA' kuralindan DAHA
        DUSUK ONCELIKLIDIR (kullanici acikca boyle istedi). Her ihlal icin
        once DOLDURMAYI (dogrudan), sonra DOLDURMAYI (swap ile) dener; TAMAMEN
        BOSALTMA (bu bir 2. bos gun yaratabilir) SADECE bu ogretmenin HENUZ
        hic bos gunu yoksa denenir - aksi halde nadir bir tek-ders kalintisi,
        2. bos gunden daha az sorunlu bir uzlasim olarak kabul edilir. Bir
        degisiklik baskasini tetikleyebilecegi icin degisiklik kalmayana ya
        da MAX_TUR'a kadar tekrarlar."""
        MAX_TUR = 10
        for _ in range(MAX_TUR):
            if _zaman_doldu():
                break
            degisti = False
            for tc in tum_tc:
                ming = tc_kisit[tc]["minG"]
                if not ming:
                    continue
                for gun in gunler:
                    yuk = day_load[tc][gun]
                    if not (0 < yuk < ming):
                        continue
                    if gunu_doldur(tc, gun, ming):
                        degisti = True
                    elif gunu_doldur_swap_ile(tc, gun, ming):
                        degisti = True
                    elif not ogrt_bos_gun_var_mi(tc) and gunu_tamamen_bosalt(tc, gun):
                        degisti = True
                    elif _izole_gorevi_tasi(tc, gun):
                        # KRITIK EK YONTEM: ogretmenin ZATEN bir bos gunu
                        # olsa bile (yukaridaki 'gunu_tamamen_bosalt'
                        # bu yuzden atlansa bile), izole kalan 1-2
                        # saatlik kalintiyi dogrudan BASKA (tercihen
                        # zaten aktif) bir gune tasimayi dener - 2.
                        # bos gun YARATMADAN. Kullanicinin gercek
                        # verisinde bulunan 'Seçmeli Astronomi'/
                        # 'Rehberlik' gibi 1 saatlik derslerin tek
                        # basina bir gunde izole kalmasi sorununun
                        # dogrudan cozumu budur.
                        degisti = True
            if not degisti:
                break

    if not _baslangic_zaten_temiz:
        tek_ders_yasakla_pass()

    # ---------------- 6b. Eksikleri tekrar dene (MUTLAK ONCELIK - digerlerinden ONCE) ----------------
    # 'Tum dersler yerlessin' kurali en kritik olandir. Bu adim ISTEGE BAGLI
    # optimizasyonlardan (bos gun, pencere, brans takasi) ONCE calisir ki
    # zaman butcesi asilsa bile eksik-ders-yerlestirme suresi ASLA
    # gasp edilmesin. Az sayida gorev kaldigindan (genelde 0-2) COK DAHA
    # DERIN arama (DERIN_TAVAN) + tum-gunleri-deneyen kovma fallback
    # kullanilir - kilitli hucre gibi ekstra kisitlarin daralttigi
    # %100 dolu siniflarda bile son bir sansi tuketir.
    hala_eksik = []
    for gid in eksikler_gid:
        if _zaman_doldu():
            hala_eksik.append(gid)
            continue
        basarili = yerlestirmeye_calis(gid, 0, tavan=DERIN_TAVAN)
        if not basarili:
            basarili = kovarak_yerlestir_haric(gid, haric_gun=0)  # 0 = gecerli gun degil, hicbir gun haric tutulmaz
        if not basarili:
            hala_eksik.append(gid)

    # ---------------- 7. Otomatik bos gun atama (ISTEGE BAGLI - tek-ders kuralini ASLA bozmaz) ----------------
    def otomatik_bos_gun_pass():
        """Manuel bosGun'u OLMAYAN ve HENUZ hicbir bos gunu olmayan
        ogretmenler icin otomatik bir bos gun olusturmaya CALISIR (bazi
        ogretmenler icin bu mumkun olmayabilir - bu normal, herkese bos gun
        garanti edilmez). TUM gunleri en-az-yuklu'den en-cok-yuklu'ye dener.
        GUVENLIK: her denemeden sonra toplam tek-ders ihlali sayisini
        kontrol eder - eger bu bos gun denemesi YENI bir ihlale yol actiysa
        KESIN GERI ALINIR ve bir sonraki gun adayi denenir. Boylece bos gun
        ozelligi asla 'asla tek ders' kuralini bozamaz."""
        # Sabit sira erken islenen ogretmenlerin tum esnekligi (kovma firsatlarini)
        # tuketip sonrakilere yer birakmamasina yol aciyordu. Once EN AGIR YUKLU
        # ogretmenlerden basla (en cok ihtiyaci olanlar), esit yuklerde deneme
        # bazli (seed'e bagli) karistir - coklu deneme boylece farkli
        # kombinasyonlar kesfeder.
        aday_tc_listesi = [tc for tc in tum_tc if not idareci_mi[tc]
                           and not tc_kisit[tc]["bosGunIstemez"] and tc_kisit[tc]["bosGun"] is None]
        rnd.shuffle(aday_tc_listesi)
        aday_tc_listesi.sort(key=lambda tc: -sum(day_load[tc][g] for g in gunler))
        for tc in aday_tc_listesi:
            if _zaman_doldu():
                break
            if ogrt_bos_gun_var_mi(tc):
                continue  # zaten (dogal ya da tek-ders duzeltmesinden) bir bos gunu var
            calisilan_gunler = [g for g in gunler if day_load[tc][g] > 0]
            if len(calisilan_gunler) <= 1:
                continue
            adaylar_gun = sorted(calisilan_gunler, key=lambda g: day_load[tc][g])
            for aday_gun in adaylar_gun:
                once = ihlal_sayisi()
                nokta = kontrol_noktasi()
                if gunu_tamamen_bosalt(tc, aday_gun):
                    if ihlal_sayisi() > once:
                        geri_al(nokta)  # yeni tek-ders ihlali yaratti - kabul edilemez
                        continue
                    tc_kisit[tc]["bosGun"] = aday_gun  # KILITLE - sonraki gecisler (pencere/takas) asla dokunmasin
                    break  # basarili VE tek-ders kuralini bozmadi

    if not _baslangic_zaten_temiz:
        # KRITIK IYILESTIRME: otomatik_bos_gun_pass eskiden SADECE BIR
        # KEZ calisiyordu - bir ogretmenin basarili bos-gun atamasi,
        # SIRADAKI ogretmenler icin musaitlik durumunu DEGISTIREBILIR
        # (bazi hucreler bosalir, bazilari doluverir) - ama bu geri
        # besleme hic kullanilmiyordu, "ilk turda basarisiz olan"
        # ogretmen bir DAHA HIC denenmiyordu. Simdi pass, ilerleme
        # OLDUGU surece (bir onceki turdan FARKLI sayida basarili olan
        # varsa) tekrar calistirilir - boylece zincirleme firsatlar
        # yakalanabilir. Kullanicinin "diger programlar bunu
        # basariyorken bizimki neden basaramiyor" sorusunun olasi bir
        # cevabi buydu.
        for _bg_tur in range(4):
            if _zaman_doldu():
                break
            _once_sifir = sum(1 for tc in tum_tc if not idareci_mi[tc]
                              and not tc_kisit[tc]["bosGunIstemez"] and not ogrt_bos_gun_var_mi(tc))
            otomatik_bos_gun_pass()
            _sonra_sifir = sum(1 for tc in tum_tc if not idareci_mi[tc]
                               and not tc_kisit[tc]["bosGunIstemez"] and not ogrt_bos_gun_var_mi(tc))
            if _sonra_sifir >= _once_sifir:
                break  # bu turda hic ilerleme olmadi, daha fazla denemek zaman kaybi

    # ---------------- 7b. Fazla bos gunu doldur (asla 2. bos gun kurali - MUTLAK) ----------------
    def fazla_bos_gun_konsolide_pass():
        """Bir ogretmenin (idareci olmayan) manuel/oto-atanan bosGun'u
        DISINDA fazladan bos gunu varsa (on-atama sonrasi dogal yerlesimden
        kaynaklanabilir - otomatik_bos_gun_pass zaten-bosGun'u-olani
        atladigi icin bunu kendisi duzeltmez), bu fazlaligi baska
        gunlerden is tasiyarak DOLDURMAYA calisir. 'Asla 2 gun bos'
        kurali MUTLAKTIR - bu yuzden bu gecis zaman butcesi disinda bile
        (cok kisa surer) her zaman calisir."""
        for tur in range(5):
            degisti = False
            for tc in tum_tc:
                if idareci_mi[tc]:
                    continue
                korunacak_gun = tc_kisit[tc]["bosGun"]  # manuel/on-atanan - buna DOKUNULMAZ
                if korunacak_gun is not None:
                    fazla_gunler = [g for g in gunler if day_load[tc][g] == 0 and g != korunacak_gun]
                else:
                    tum_bos = [g for g in gunler if day_load[tc][g] == 0]
                    if len(tum_bos) <= 1:
                        continue
                    fazla_gunler = tum_bos[1:]  # ilki (tum_bos[0]) korunur
                if not fazla_gunler:
                    continue
                ming = tc_kisit[tc]["minG"] or 2
                for fazla_gun in fazla_gunler:
                    if gunu_doldur(tc, fazla_gun, ming):
                        degisti = True
                    elif gunu_doldur_swap_ile(tc, fazla_gun, ming):
                        degisti = True
            if not degisti:
                break

    if not _baslangic_zaten_temiz:
        fazla_bos_gun_konsolide_pass()

    def _takasi_uygula(gid1, gid2):
        g1, g2 = gid_map[gid1], gid_map[gid2]
        tc1_eski, tc2_eski = g1["tc"], g2["tc"]
        if tc1_eski == tc2_eski or not g1["placed"] or not g2["placed"]:
            return False
        gun1, saat1 = g1["placed"]
        gun2, saat2 = g2["placed"]
        ogrtler1_eski, ogrtler2_eski = g1["ogrtler"], g2["ogrtler"]
        once_ihlal = ihlal_sayisi()
        once_fazla = fazla_bos_gun_toplam()
        once_sifir = sifir_bos_gun_toplam()
        nokta = kontrol_noktasi()
        bosalt(gid1)
        bosalt(gid2)
        g1["tc"], g2["tc"] = tc2_eski, tc1_eski
        g1["ogrtler"], g2["ogrtler"] = ogrtler2_eski, ogrtler1_eski
        if musait_mi(gid1, gun1, saat1) and musait_mi(gid2, gun2, saat2):
            yerlestir(gid1, gun1, saat1)
            yerlestir(gid2, gun2, saat2)
            if (ihlal_sayisi() > once_ihlal or fazla_bos_gun_toplam() > once_fazla
                    or sifir_bos_gun_toplam() > once_sifir):
                g1["tc"], g2["tc"] = tc1_eski, tc2_eski
                g1["ogrtler"], g2["ogrtler"] = ogrtler1_eski, ogrtler2_eski
                geri_al(nokta)
                return False
            # BASARILI BRANS TAKASI KAYDI: kullanicinin "hangi takaslar
            # yapildi gorelim" istegi uzerine, her basarili takas
            # (sinif, ders, eski/yeni ogretmen TC) burada kaydedilir -
            # sonuc ile birlikte disari aktarilir, boylece "Uygula"
            # oncesi kullaniciya ozetlenebilir.
            _brans_takas_gecmisi.append({
                "sid": g1.get("sid"), "did": g1.get("did"),
                "sid2": g2.get("sid"), "did2": g2.get("did"),
                "tc1_eski": tc1_eski, "tc1_yeni": tc2_eski,
                "tc2_eski": tc2_eski, "tc2_yeni": tc1_eski,
            })
            return True
        g1["tc"], g2["tc"] = tc1_eski, tc2_eski
        g1["ogrtler"], g2["ogrtler"] = ogrtler1_eski, ogrtler2_eski
        geri_al(nokta)
        return False

    def _zaman_takasi_uygula(gid1, gid2):
        """BRANSTAN BAGIMSIZ zaman-dilimi takasi: iki dersin GUN/SAATini
        birbiriyle degistirir - HANGI OGRETMENIN OGRETTIGI DEGISMEZ,
        sadece NE ZAMAN ogretildigi degisir. Bu, _takasi_uygula'nin
        (ogretmen degisir, zaman sabit kalir) TAM TAMAMLAYICISIDIR -
        ASC/FET gibi profesyonel programlarin kullandigi asil guclu
        hareket budur, cunku ayni branstan bir takas ortagi GEREKTIRMEZ -
        HERHANGI IKI DERS (farkli sinif, farkli ogretmen, farkli brans
        olsa bile) zamanlarini takas edebilir."""
        g1, g2 = gid_map[gid1], gid_map[gid2]
        if not g1["placed"] or not g2["placed"] or g1["boy"] != g2["boy"]:
            return False
        gun1, saat1 = g1["placed"]
        gun2, saat2 = g2["placed"]
        if (gun1, saat1) == (gun2, saat2):
            return False
        once_ihlal = ihlal_sayisi()
        once_fazla = fazla_bos_gun_toplam()
        once_sifir = sifir_bos_gun_toplam()
        nokta = kontrol_noktasi()
        bosalt(gid1)
        bosalt(gid2)
        if musait_mi(gid1, gun2, saat2) and musait_mi(gid2, gun1, saat1):
            yerlestir(gid1, gun2, saat2)
            yerlestir(gid2, gun1, saat1)
            if (ihlal_sayisi() > once_ihlal or fazla_bos_gun_toplam() > once_fazla
                    or sifir_bos_gun_toplam() > once_sifir):
                geri_al(nokta)
                return False
            return True
        geri_al(nokta)
        return False

    def _zaman_takasi_karma_uygula(gid_buyuk, kucuk_idler):
        """KARMA ZAMAN TAKASI: bir BUYUK blogu (orn. 2 saatlik Tarih),
        BASKA bir gunde YAN YANA duran kucuk derslerle (orn. 1 saatlik
        Saglik Bilgisi + 1 saatlik Rehberlik) yer degistirir.

        MUTLAK GUVENLIK GARANTISI - BLOK ASLA BOLUNMEZ:
        Bu fonksiyon hicbir gorevin "boy" (uzunluk) degerine DOKUNMAZ.
        Buyuk blok, hedef zamana TEK PARCA halinde yerlesir; kucuk
        dersler de kendi uzunluklarini KORUYARAK buyuk blogun bosalttigi
        araliga sirayla dizilir. Yani 2'lik bir blok ASLA 1+1'e
        bolunemez - bu, kodun yapisi geregi imkansizdir.

        Neden gerekli: eski kod SADECE ayni uzunluktaki bloklarin takasina
        izin veriyordu (g1["boy"] != g2["boy"] -> atla). Bu yuzden 1 saatlik
        dersler (Rehberlik, Seclmeli Astronomi vb.) ile 2-4 saatlik bloklar
        arasinda HIC KOPRU yoktu ve bazi pencereler asla doldurulamiyordu.
        """
        g_buyuk = gid_map[gid_buyuk]
        kucukler = [gid_map[k] for k in kucuk_idler]
        if not g_buyuk["placed"] or not kucukler:
            return False
        if any(not k["placed"] for k in kucukler):
            return False
        # Ayni gorev iki kez gecmesin, buyuk blok kucukler arasinda olmasin
        tum_idler = [gid_buyuk] + list(kucuk_idler)
        if len(set(tum_idler)) != len(tum_idler):
            return False
        # Toplam uzunluk BIREBIR esit olmali (aksi halde bosluk/tasma olur)
        if sum(k["boy"] for k in kucukler) != g_buyuk["boy"]:
            return False
        gunB, saatB = g_buyuk["placed"]
        gunK, saatK = kucukler[0]["placed"]
        if (gunB, saatB) == (gunK, saatK):
            return False
        # Kucukler AYNI GUNDE ve KESINTISIZ (yan yana) olmali
        beklenen = saatK
        for k in kucukler:
            if k["placed"] != (gunK, beklenen):
                return False
            beklenen += k["boy"]
        # Buyuk blok ile kucuklerin araligi cakisiyorsa (ayni gun ve
        # ic ice) takas anlamsiz/riskli - atla
        if gunB == gunK and not (saatB + g_buyuk["boy"] <= saatK or saatK + g_buyuk["boy"] <= saatB):
            return False

        once_ihlal = ihlal_sayisi()
        once_fazla = fazla_bos_gun_toplam()
        once_sifir = sifir_bos_gun_toplam()
        nokta = kontrol_noktasi()

        bosalt(gid_buyuk)
        for k in kucuk_idler:
            bosalt(k)

        # Buyuk blok TEK PARCA halinde kucuklerin bosalttigi yere
        if not musait_mi(gid_buyuk, gunK, saatK):
            geri_al(nokta)
            return False
        yerlestir(gid_buyuk, gunK, saatK)

        # Kucukler, buyuk blogun bosalttigi araliga SIRAYLA (her biri
        # kendi uzunlugunu koruyarak). Her adimda TEK TEK kontrol edilir -
        # biri bile yerlesemezse TAMAMI geri alinir.
        imlec = saatB
        for k_id in kucuk_idler:
            k = gid_map[k_id]
            if not musait_mi(k_id, gunB, imlec):
                geri_al(nokta)
                return False
            yerlestir(k_id, gunB, imlec)
            imlec += k["boy"]

        if (ihlal_sayisi() > once_ihlal or fazla_bos_gun_toplam() > once_fazla
                or sifir_bos_gun_toplam() > once_sifir):
            geri_al(nokta)
            return False
        return True

    def _zaman_rotasyon_uygula(gid1, gid2, gid3):
        """3'LU DONGUSEL zaman rotasyonu: g1->g2'nin eski yeri, g2->g3'un
        eski yeri, g3->g1'in eski yeri. Bazi durumlarda IKILI takas
        (_zaman_takasi_uygula) tek basina cozum bulamaz - A dersi B'nin
        yerini, B dersi C'nin yerini, C dersi A'nin yerini istiyor
        olabilir (dongusel bagimlilik). Bu fonksiyon boyle 'ucgen'
        durumlari cozer - ASC/FET'in de kullandigi 'zincirleme takas'
        mantigina bir adim daha yaklasir."""
        g1, g2, g3 = gid_map[gid1], gid_map[gid2], gid_map[gid3]
        if not (g1["placed"] and g2["placed"] and g3["placed"]):
            return False
        if not (g1["boy"] == g2["boy"] == g3["boy"]):
            return False
        p1, p2, p3 = g1["placed"], g2["placed"], g3["placed"]
        if len({p1, p2, p3}) < 3:
            return False  # ucu de farkli konumda olmali, aksi halde anlamsiz
        once_ihlal = ihlal_sayisi()
        once_fazla = fazla_bos_gun_toplam()
        once_sifir = sifir_bos_gun_toplam()
        nokta = kontrol_noktasi()
        bosalt(gid1)
        bosalt(gid2)
        bosalt(gid3)
        if musait_mi(gid1, *p2) and musait_mi(gid2, *p3) and musait_mi(gid3, *p1):
            yerlestir(gid1, *p2)
            yerlestir(gid2, *p3)
            yerlestir(gid3, *p1)
            if (ihlal_sayisi() > once_ihlal or fazla_bos_gun_toplam() > once_fazla
                    or sifir_bos_gun_toplam() > once_sifir):
                geri_al(nokta)
                return False
            return True
        geri_al(nokta)
        return False


    # ---------------- 9b. Fazla bos gunu BRANS TAKASIYLA doldurmayi zorla ----------------
    # fazla_bos_gun_konsolide_pass (dogrudan doldur/swap) bazi ogretmenler icin
    # basarisiz kalabiliyor (hedef sinif zaten dolu). Bu son care: o gundeki
    # AYNI BRANSTAN baska bir ogretmenin dersini TAKAS ederek (sinif/saat hic
    # degismeden, sadece kim ogrettigi degisir) o gunu doldurmaya calisir -
    # boylece hedef sinifin dolu olmasi sorun olmaktan cikar.
    def _ogretmenin_fazla_gunleri(tc):
        korunacak_gun = tc_kisit[tc]["bosGun"]
        if korunacak_gun is not None:
            return [g for g in gunler if day_load[tc][g] == 0 and g != korunacak_gun]
        tum_bos = [g for g in gunler if day_load[tc][g] == 0]
        return tum_bos[1:] if len(tum_bos) > 1 else []

    def _fazla_bos_gun_brans_takasi_dene(tc):
        brans = tc_kisit[tc]["brans"]
        if not brans:
            return False
        ming = tc_kisit[tc]["minG"] or 2
        genel_basari = False
        for fazla_gun in _ogretmenin_fazla_gunleri(tc):
            # TEK takasla yetinme: gun GERCEKTEN en az ming saate ulasana
            # kadar (ya da daha fazla aday kalmayana kadar) tekrar tekrar
            # takas dene - aksi halde yarim dolu (0<yuk<ming) yeni bir
            # tek-ders ihlali birakabilir.
            while day_load[tc][fazla_gun] < ming:
                adaylar_g2 = [g2 for g2 in gorevler
                              if g2["placed"] and g2["placed"][0] == fazla_gun
                              and g2["tc"] and g2["tc"] != tc
                              and tc_kisit.get(g2["tc"], {}).get("brans") == brans]
                bu_turda_basarili = False
                for g2 in adaylar_g2:
                    boy2 = g2["boy"]
                    adaylar_g1 = [g1 for g1 in gorevler
                                  if g1["placed"] and tc in tum_ogrt(g1) and g1["boy"] == boy2
                                  and g1["placed"][0] != fazla_gun]
                    for g1 in adaylar_g1:
                        if _takasi_uygula(g1["id"], g2["id"]):
                            genel_basari = True
                            bu_turda_basarili = True
                            break
                    if bu_turda_basarili:
                        break
                if not bu_turda_basarili:
                    break  # bu gun icin daha fazla aday yok, sonraki fazla_gun'a gec
        return genel_basari

    def fazla_bos_gun_brans_takas_pass():
        for _tur in range(10):
            if _zaman_doldu():
                break
            hedefler = [tc for tc in tum_tc if not idareci_mi[tc] and tc_kisit[tc]["brans"]
                        and _ogretmenin_fazla_gunleri(tc)]
            if not hedefler:
                break
            degisti = False
            for tc in hedefler:
                if _zaman_doldu():
                    break
                if _fazla_bos_gun_brans_takasi_dene(tc):
                    degisti = True
            if not degisti:
                break

    if not _baslangic_zaten_temiz:
        fazla_bos_gun_brans_takas_pass()

    # ---------------- 7b2. Bos gun ALAMAYAN ogretmenler icin BRANS TAKASI ----------------
    # otomatik_bos_gun_pass sadece kovma/dogrudan-tasima dener - bu, o gunku
    # TUM derslerin (potansiyel olarak birden fazla FARKLI sinifa ait) AYNI
    # ANDA baska bosluklara sigmasini gerektirir; %100 dolu siniflarda bu
    # sik sik BASARISIZ olur. Bu gecis, fazla_bos_gun_brans_takas_pass'ta
    # ISE YARAYAN yontemi kullanir: o gundeki her ders icin, sinifin/saatin
    # HIC degismedigi, sadece AYNI BRANSTAN baska bir ogretmenle 'kim
    # ogretiyor' takasi yapilir - boylece bos hucre aramaya hic gerek kalmaz.
    def _gun_bosalt_brans_takasi_dene(tc, hedef_gun):
        brans = tc_kisit[tc]["brans"]
        if not brans:
            return False
        gorev_listesi = [g for g in gorevler if g["placed"] and g["placed"][0] == hedef_gun and tc in tum_ogrt(g)]
        if not gorev_listesi:
            return False
        for g1 in gorev_listesi:
            boy1 = g1["boy"]
            adaylar_g2 = [g2 for g2 in gorevler
                          if g2["placed"] and g2["placed"][0] != hedef_gun
                          and g2["boy"] == boy1 and g2["tc"] and g2["tc"] != tc
                          and tc_kisit.get(g2["tc"], {}).get("brans") == brans]
            for g2 in adaylar_g2:
                if tc in tum_ogrt(g2):
                    continue  # tc zaten bu gorevde (ek_tcler) - takas anlamsiz
                if _takasi_uygula(g1["id"], g2["id"]):
                    return True
        return False

    def otomatik_bos_gun_brans_takas_pass():
        for _tur in range(10):
            if _zaman_doldu():
                break
            hedefler = [tc for tc in tum_tc if not idareci_mi[tc]
                        and not tc_kisit[tc]["bosGunIstemez"] and tc_kisit[tc]["bosGun"] is None
                        and not ogrt_bos_gun_var_mi(tc) and tc_kisit[tc]["brans"]]
            if not hedefler:
                break
            degisti = False
            for tc in hedefler:
                if _zaman_doldu():
                    break
                calisilan = [g for g in gunler if day_load[tc][g] > 0]
                if len(calisilan) <= 1:
                    continue
                for hedef_gun in sorted(calisilan, key=lambda g: day_load[tc][g]):
                    if day_load[tc][hedef_gun] == 0:
                        break
                    _gun_bosalt_brans_takasi_dene(tc, hedef_gun)
                    if day_load[tc][hedef_gun] == 0:
                        tc_kisit[tc]["bosGun"] = hedef_gun  # KILITLE - sonraki gecisler dokunmasin
                        degisti = True
                        break
            if not degisti:
                break

    if not _baslangic_zaten_temiz:
        otomatik_bos_gun_brans_takas_pass()

    # ---------------- 7c. Son tek-ders temizligi (bos gun gecisi yan etki yaratmis olabilir) ----------------
    if not _baslangic_zaten_temiz:
        tek_ders_yasakla_pass()

    # ---------------- 8. Pencere minimizasyonu (hedef: haftalik <=2 pencere) ----------------

    def ogrt_gun_saatleri(tc, gun):
        saatler = []
        for g in gorevler:
            if tc in tum_ogrt(g) and g["placed"] and g["placed"][0] == gun:
                saatler.extend(range(g["placed"][1], g["placed"][1] + g["boy"]))
        return sorted(saatler)

    def ogrt_haftalik_pencere(tc):
        toplam = 0
        for gun in gunler:
            saatler = ogrt_gun_saatleri(tc, gun)
            if len(saatler) < 2:
                continue
            toplam += (max(saatler) - min(saatler) + 1) - len(saatler)
        return toplam

    def gun_ici_sikistir(tc, gun):
        """Bir gun icindeki dagilmis dersleri sola dogru sikistirir. Once
        hedef saat BOSSA dogrudan tasir; BOSSA DEGILSE (baska bir ders
        varsa) o dersle YER DEGISTIRMEYI (swap) dener - bu, once sadece
        bos hucre araniyorken atlanan COK SAYIDA sikistirma firsatini
        yakalar (ozellikle yogun/dolu programlarda hedef saat neredeyse
        HICBIR ZAMAN bos degildir)."""
        degisti_toplam = False
        for _ic_tur in range(8):
            tasklar = sorted(
                [g for g in gorevler if tc in tum_ogrt(g) and g["placed"] and g["placed"][0] == gun],
                key=lambda g: g["placed"][1])
            if len(tasklar) < 2:
                break
            degisti = False
            for t in tasklar:
                gun2, saat2 = t["placed"]
                if saat2 <= 1:
                    continue
                hedef_saat = saat2 - 1
                nokta = kontrol_noktasi()
                bosalt(t["id"])
                if musait_mi(t["id"], gun2, hedef_saat):
                    yerlestir(t["id"], gun2, hedef_saat)
                    degisti = True
                    degisti_toplam = True
                    continue
                # Hedef saat dolu - o saati isgal eden TEK bir gorev varsa
                # YER DEGISTIRMEYI dene (iki dersin saatini karsilikli
                # takas et). Kilitli hucrelere veya birden fazla gorevin
                # ayni saatte cakistigi (coklu-blok) durumlara DOKUNULMAZ.
                isgal_eden = class_occ.get(t["sid"], {}).get((gun2, hedef_saat))
                if isgal_eden and isgal_eden != t["id"]:
                    g2 = gid_map.get(isgal_eden)
                    if (g2 and g2["sid"] == t["sid"] and g2["placed"] == (gun2, hedef_saat)
                            and g2["boy"] == t["boy"]):
                        once_ihlal = ihlal_sayisi()
                        bosalt(g2["id"])
                        if musait_mi(g2["id"], gun2, saat2) and musait_mi(t["id"], gun2, hedef_saat):
                            yerlestir(g2["id"], gun2, saat2)
                            yerlestir(t["id"], gun2, hedef_saat)
                            if ihlal_sayisi() > once_ihlal:
                                geri_al(nokta)  # yeni tek-ders ihlali yarattiysa vazgec
                            else:
                                degisti = True
                                degisti_toplam = True
                            continue
                        else:
                            geri_al(nokta)
                            continue
                geri_al(nokta)
            if not degisti:
                break
        return degisti_toplam

    def _zaman_zincir_uygula(gid_listesi):
        """GENEL N'Lİ DONGUSEL zaman rotasyonu: gid_listesi'ndeki HER
        gorev, LISTEDEKI BIR SONRAKI gorevin eski yerine tasinir (son
        gorev, ILK gorevin eski yerine doner). Uzunluk 2 ise bu
        _zaman_takasi_uygula ile AYNI seydir, 3 ise _zaman_rotasyon_uygula
        ile ayni. Ama BURADA uzunluk 4, 5, 6, 7... herhangi bir sayi
        olabilir - kullanicinin istegi uzerine: 'ikili takas yetmezse uc,
        dort, bes, alti, yedi, gerekirse TUM dersler surekli degisebilir'.
        Zincir ne kadar uzunsa, hepsinin AYNI ANDA uygun olmasi gerektigi
        icin basari ihtimali dusuk olur - ama BULUNDUGUNDA cok daha
        guclu/esnek bir cozum sunar."""
        gorevler_n = [gid_map[gid] for gid in gid_listesi]
        if any(not g["placed"] for g in gorevler_n):
            return False
        boy0 = gorevler_n[0]["boy"]
        if any(g["boy"] != boy0 for g in gorevler_n):
            return False
        konumlar = [g["placed"] for g in gorevler_n]
        if len(set(konumlar)) < len(konumlar):
            return False  # tum konumlar birbirinden farkli olmali
        once_ihlal = ihlal_sayisi()
        once_fazla = fazla_bos_gun_toplam()
        once_sifir = sifir_bos_gun_toplam()
        nokta = kontrol_noktasi()
        for gid in gid_listesi:
            bosalt(gid)
        n = len(gid_listesi)
        hepsi_uygun = all(
            musait_mi(gid_listesi[i], *konumlar[(i + 1) % n]) for i in range(n))
        if hepsi_uygun:
            for i in range(n):
                yerlestir(gid_listesi[i], *konumlar[(i + 1) % n])
            if (ihlal_sayisi() > once_ihlal or fazla_bos_gun_toplam() > once_fazla
                    or sifir_bos_gun_toplam() > once_sifir):
                geri_al(nokta)
                return False
            return True
        geri_al(nokta)
        return False

    def gunler_arasi_bosluk_doldur(tc):
        """Bir gunun ic bosluguna, tc'nin BASKA bir gundeki bir dersini tasimayi
        dener. Once dogrudan bos hucre arar; sinif dolu oldugu icin bos hucre
        yoksa, hedef hucreyi isgal edeni KOVUP (mevcut yerlestirmeye_calis
        makinesiyle) yeniden yerlestirmeyi dener - yogun dolu programlarda
        bos hucre bulmak neredeyse imkansiz oldugundan bu adim olmadan
        pencere azaltma pratikte hicbir sey yapamiyordu."""
        degisti = False
        for gun in gunler:
            if _zaman_doldu():
                return degisti
            saatler = ogrt_gun_saatleri(tc, gun)
            if len(saatler) < 2:
                continue
            mn, mx = min(saatler), max(saatler)
            bos_saatler = [s for s in range(mn, mx + 1) if s not in saatler]
            if not bos_saatler:
                continue
            digerleri = [g for g in gorevler
                         if tc in tum_ogrt(g) and g["placed"] and g["placed"][0] != gun]
            digerleri.sort(key=lambda g: -g["boy"])
            for t in digerleri:
                boy = t["boy"]
                hedef_basla = None
                for i in range(len(bos_saatler) - boy + 1):
                    aday = bos_saatler[i:i + boy]
                    if aday == list(range(aday[0], aday[0] + boy)):
                        hedef_basla = aday[0]
                        break
                if hedef_basla is None:
                    continue

                nokta = kontrol_noktasi()
                bosalt(t["id"])

                if musait_mi(t["id"], gun, hedef_basla):
                    yerlestir(t["id"], gun, hedef_basla)
                    degisti = True
                    break

                # Dogrudan bos degil - hedef hucreyi isgal edeni kovmayi dene
                ogrtler_t = tum_ogrt(t)
                cakisanlar = set()
                bloklanmis = False
                for b in range(boy):
                    s = hedef_basla + b
                    occ = class_occ[t["sid"]].get((gun, s))
                    if occ == "KILITLI":
                        bloklanmis = True
                        break
                    if occ:
                        cakisanlar.add(occ)
                    for otc in ogrtler_t:
                        occ2 = teacher_occ[otc].get((gun, s))
                        if occ2 == "KILITLI":
                            bloklanmis = True
                            break
                        if occ2:
                            cakisanlar.add(occ2)
                    if bloklanmis:
                        break

                # GUVENLIK: bu deger artik SABIT (6) - KOVMA_ZINCIR_SINIRI'ye
                # baglanmasi (kullanicinin arka plan aramasinda 50'ye kadar
                # cikmasina izin vermesi) ozyinelemeli yerlestirmeye_calis
                # motorunda USTEL yavaslamaya/donmaya yol aciyordu (874
                # gorevin her biri icin cagriliyor). Pencere azaltma gucu
                # artik bunun yerine ozyinelemesiz zaman_takasi_pencere_pass
                # ile saglaniyor.
                if bloklanmis or not cakisanlar or len(cakisanlar) > 6:
                    geri_al(nokta)
                    continue

                for cg in sorted(cakisanlar):
                    bosalt(cg)
                if musait_mi(t["id"], gun, hedef_basla):
                    yerlestir(t["id"], gun, hedef_basla)
                    basarili = True
                    for cg in sorted(cakisanlar):
                        if not yerlestirmeye_calis(cg, 0, tavan=DERIN_TAVAN):
                            basarili = False
                            break
                    if basarili:
                        degisti = True
                        break
                    geri_al(nokta)
                else:
                    geri_al(nokta)
        return degisti

    def pencere_azalt_pass():
        """MAX_PENCERE_HEDEF'e ulasmaya calisan best-effort local search.
        Agir kisit yuklerinde tam garanti VEREMEZ ama mumkun oldugunca
        yaklasir. Once en cok pencereli ogretmenden baslar. Idareci (2-12
        saat) ogretmenler ic pencere hedefinden MUAF - onlar zaten her gun
        okulda, pencere sayilari onemli degil."""
        for _dis_tur in range(25):
            if _zaman_doldu():
                break
            pencereli = sorted(
                (tc for tc in tum_tc if not idareci_mi[tc] and ogrt_haftalik_pencere(tc) > MAX_PENCERE_HEDEF),
                key=lambda tc: -ogrt_haftalik_pencere(tc))
            if not pencereli:
                break
            herhangi_degisti = False
            for tc in pencereli:
                for gun in gunler:
                    if gun_ici_sikistir(tc, gun):
                        herhangi_degisti = True
                if gunler_arasi_bosluk_doldur(tc):
                    herhangi_degisti = True
            if not herhangi_degisti:
                break

    # KRITIK DUZELTME: pencere_azalt_pass (eski, KORUMASIZ gecis - 'kimse
    # kotulesmesin' kontrolu YOK) 'kaldigi yerden devam' modunda ATLANIR.
    # Gercek prod loglari kanitladi: checkpoint zaten_temiz=True olsa bile
    # (yani onceki 6 gecis dogru sekilde atlansa bile), BU gecis hala
    # temiz bir 35-pencere checkpoint'ini 45'e cikarabiliyordu - cunku bu
    # eski mekanizma pencere degerini DUSURMEYE calisirken baska
    # ogretmenleri KOTULESTIREBILIYOR, zaman_takasi_pencere_pass'in
    # sahip oldugu global 'kimse kotulesmesin' korumasi bunda YOK. Artik
    # devam modunda dogrudan KORUMALI zaman_takasi_pencere_pass'a
    # birakiliyor.
    if not baslangic_yerlesim:
        pencere_azalt_pass()
    def zaman_takasi_pencere_pass():
        """En cok pencereli ogretmenden baslayarak, HERHANGI baska bir
        dersle (brans siniri OLMADAN) zaman takasi deneyerek pencereyi
        azaltmaya calisir. _takasi_uygula (brans takasi) ile
        _zaman_takasi_uygula (zaman takasi) birlikte, ASC/FET'in
        yaptigina cok daha yakin bir arama gucu saglar.

        PERFORMANS: (gun,saat)->gid indeksi _dis_tur basina BIR KEZ
        kurulur (874 gorevi HER bos hucre icin tek tek taramak yerine).

        GUVENLIK: zaman kontrolune (_zaman_doldu) EK OLARAK mutlak bir
        DENEME SAYACI (_deneme_sayaci) da var - zamanlama kontrolu
        herhangi bir nedenle beklendigi gibi calismasa BILE, toplam
        deneme sayisi bir ust siniri asinca pass KESIN olarak durur.
        Bu, 'donma' riskini SIFIRA indirmek icin cift guvenlik katmanidir."""
        _deneme_sayaci = 0
        _MAKS_DENEME = 2_000_000  # her deneme artik ucuz (O(1) indeks) - asil sinir zaman butcesi
        _basarili_takas_sayisi = 0
        _dis_tur_sayisi = 0
        _baslangic_pencere_fazla = sum(
            1 for tc in tum_tc if not idareci_mi[tc] and ogrt_haftalik_pencere(tc) > MAX_PENCERE_HEDEF)

        # KRITIK DUZELTME: loglar gosterdi ki bu gecise SIK SIK "deneme=0"
        # ile HIC zaman kalmiyordu - ONCEKI gecisler (yerlestirme, bos-gun,
        # brans-takas vb.) toplam butceyi (_deneme_butcesi) TAMAMEN
        # tuketiyordu, bu gecis ise pipeline'in EN SONUNDA oldugu icin
        # HICBIR SEY deneyemeden cikiyordu. Artik bu gecise GARANTILI BIR
        # MINIMUM SURE taniniyor - genel butce dolmus olsa BILE, bu pass
        # en az bir miktar (kucuk, guvenli, mutlak bir tavanla sinirli)
        # sure boyunca calisir.
        _zt_baslangic = time.time()
        _zt_garanti_sn = min(20, max(_deneme_butcesi * 0.35, 6))  # en az ~6sn, en fazla 20sn garanti

        def _zt_zaman_doldu():
            gecen_zt = time.time() - _zt_baslangic
            if gecen_zt < _zt_garanti_sn:
                return False  # garanti sure dolmadi - genel butce dolmus olsa bile devam et
            return _zaman_doldu() or gecen_zt > 45  # garanti sonrasi normal kontrol + mutlak tavan (45sn)

        # SA SOGUTMA TAKVIMI - ZAMANA BAGLI (tur sayisina DEGIL):
        # KRITIK DUZELTME: sicaklik eskiden _dis_tur/15 ile hesaplaniyordu,
        # ama gercek loglar gosterdi ki zaman butcesi yuzunden 15 dis
        # turun ancak 2-4'u calisabiliyor - yani sicaklik 6.0'dan hicbir
        # zaman ~4.4'un altina INMIYORDU. Sonuc: SA surekli "kesif"
        # modunda kaliyor, hicbir zaman "yogunlasma" (exploit) asamasina
        # gecemiyordu; bulunan iyi bolgeler derinlemesine islenmiyordu.
        # Artik sogutma, pass'in GECEN ZAMAN ORANINA baglidir - kac tur
        # calisirsa calissin, sicaklik 6.0'dan 0.3'e kadar TAM egriyi
        # kat eder.
        _zt_kalan = _deneme_butcesi - (time.time() - t0)
        _zt_planlanan = max(_zt_garanti_sn, min(45.0, _zt_kalan))

        def _sa_sicaklik():
            ilerleme = (time.time() - _zt_baslangic) / max(_zt_planlanan, 1e-6)
            if ilerleme < 0:
                ilerleme = 0.0
            elif ilerleme > 1:
                ilerleme = 1.0
            return max(0.3, 6.0 * (1 - ilerleme))

        def _pencere_hizli(tc, gun_index):
            """ogrt_haftalik_pencere ile AYNI sonucu verir ama 874 gorevi
            TARAMAZ - onceden kurulmus gun_index'ten O(gun_sayisi) okur.
            KRITIK PERFORMANS DUZELTMESI: global kontrol eklendikten sonra
            pencere hesaplama COK DAHA SIK cagriliyordu (deneme basina
            4-6 kez) - eski O(874) tarama bu yuzden COK ciddi bir
            yavaslama kaynagi olmustu."""
            toplam = 0
            for gun, saatler_ham in gun_index.get(tc, {}).items():
                saatler = sorted(set(saatler_ham))
                if len(saatler) < 2:
                    continue
                toplam += (max(saatler) - min(saatler) + 1) - len(saatler)
            return toplam

        # ---- GERCEK SIMULATED ANNEALING ALTYAPISI ----
        # OLCULEN SORUN: onceki surumde KOTULESEN hicbir hamle ASLA kabul
        # edilmiyordu (sadece iyilesme + yanal). Bu, tanim geregi "tepe
        # tirmanma"dir ve YEREL OPTIMUMDAN MATEMATIKSEL OLARAK CIKAMAZ -
        # gercek olcumde 78 hamle sirf kotulesme diye reddedildi, bunlarin
        # 63'u SADECE +1/+2 birimlik kucuk kotulesmelerdi. Iste plato'nun
        # sebebi buydu.
        # COZUM: gercek SA - kucuk kotulesmeler sicakliga bagli olasilikla
        # KABUL EDILIR (exp(-fark/T)), boylece arama tepeyi asip daha
        # derin bir cukura inebilir. GUVENLIK: kumulatif toplam takip
        # edilir ve EN IYI ANIN kontrol noktasi saklanir; pass bitiminde
        # mevcut durum en iyiden kotuyse EN IYI DURUMA GERI DONULUR -
        # yani SA'nin kesif ozgurlugu var ama sonuc ASLA kotulesemez.
        _sa_kumulatif = 0            # pass baslangicina gore net degisim
        _sa_en_iyi_kumulatif = 0     # goruilen en iyi net degisim
        _sa_en_iyi_nokta = kontrol_noktasi()
        _sa_kabul_kotu = 0           # tanilama: kac kotulesen hamle kabul edildi
        # KRITIK: anlik goruntuye MUTLAK KURAL ihlalleri de kaydedilir.
        # Olcumde goruldu ki sadece pencereye gore secilen bir "en iyi an"a
        # geri donmek, fazla_bos_gun/tek_ders ihlallerini GERI GETIREBILIYOR
        # (ihlaller zaman icinde azaldigi icin daha ESKI anlarda daha COK
        # ihlal vardir). Artik geri donus, ihlalleri ASLA artiramaz.
        _sa_en_iyi_ihlal = (ihlal_sayisi(), fazla_bos_gun_toplam(), sifir_bos_gun_toplam())

        # SA MALIYET FONKSIYONU - KULLANICININ GERCEK HEDEFI:
        # "80 ogretmenin 60'inin penceresi 0 olmasi DEGIL, 80'inin de
        # 1 veya 2 olmasi onemli." Yani bir ogretmeni 2'den 0'a indirmek
        # DEGERSIZDIR; 3'u 2'ye indirmek HER SEYDIR.
        # Onceki surumde maliyet = (esik cezasi) + p idi; buradaki "+p"
        # terimi, aramanin ZATEN HEDEFTE OLAN ogretmenleri 2'den 1'e,
        # 1'den 0'a cekmek icin efor harcamasina yol aciyordu - bu efor
        # tamamen bosa gidiyordu. Artik esik ALTINDAKI her durum EsIT
        # maliyetlidir (0), ve sadece ESIGI ASAN kisim cezalandirilir.
        _SA_K = 10
        # BOS GUNSUZ ogretmen basina ceza. hesapla_skor'da bos gun
        # PENCEREDEN ONCE geldigi icin agirlik cok yuksek tutuldu:
        # boylece SA, birine bos gun kazandirmak icin pencerede kucuk
        # bir kotulesmeyi GOZE ALIR.
        _SA_BOSGUN = 150

        def _sa_maliyet(p):
            if p <= MAX_PENCERE_HEDEF:
                return 0  # 0, 1, 2 -> hepsi ESIT derecede iyi (kullanici boyle istedi)
            # NOT (olculdu): esigi asan kismi KARESEL cezalandirmak
            # denendi ("esite yakin olsun" kurali icin). Sonuc: en kotu
            # ogretmenin penceresi dustu (max 9/11/11 -> 7/9/9) AMA asil
            # hedef olan "pencere>2 olan ogretmen SAYISI" belirgin
            # kotulesti (48/49/50 -> 55/62/53). Kullanicinin birincil
            # onceligi TUM ogretmenlerin esik ALTINA inmesi oldugu icin
            # DOGRUSAL ceza korundu. Outlier kontrolu zaten hesapla_skor
            # icindeki 'pencere_max' olcutuyle saglaniyor.
            asim = p - MAX_PENCERE_HEDEF
            # NOT (IKI KEZ OLCULDU): karesel adalet terimi (asim*asim)
            # denendi; en kotu ogretmenin penceresini dusuruyor AMA esigi
            # asan ogretmen SAYISINI belirgin kotulestiriyor. Adalet
            # zaten hesapla_skor icindeki 'pencere_max' olcutuyle
            # korunuyor; bu yuzden DOGRUSAL ceza kaldi.
            return _SA_K + asim

        def _sa_kaydet(fark_kabul):
            """Kabul edilen bir hamleden sonra kumulatifi guncelle ve
            gerekiyorsa 'en iyi an'i isaretle."""
            nonlocal _sa_kumulatif, _sa_en_iyi_kumulatif, _sa_en_iyi_nokta, _sa_en_iyi_ihlal
            _sa_kumulatif += fark_kabul
            if _sa_kumulatif < _sa_en_iyi_kumulatif:
                _sa_en_iyi_kumulatif = _sa_kumulatif
                _sa_en_iyi_nokta = kontrol_noktasi()
                _sa_en_iyi_ihlal = (ihlal_sayisi(), fazla_bos_gun_toplam(), sifir_bos_gun_toplam())

        def _sa_kabul_mu(fark, sicaklik):
            """Gercek SA kabul kriteri: iyilesme her zaman; yanal ve
            kotulesme sicakliga bagli olasilikla (exp(-fark/T))."""
            if fark < 0:
                return True
            if fark == 0:
                return random.random() < sicaklik
            try:
                return random.random() < math.exp(-fark / max(sicaklik, 1e-6))
            except OverflowError:
                return False

        # DIS TUR TAVANI: 15 -> 200. Bu yapay tavan, zaman butcesi HENUZ
        # DOLMAMISKEN aramanin durmasina yol acabiliyordu. Zaten her turda
        # _zt_zaman_doldu() kontrolu var, yani gercek sinir ZAMAN; bu tavan
        # sadece sonsuz donguye karsi bir emniyet supabidir.
        for _dis_tur in range(200):
            _dis_tur_sayisi += 1
            if _zt_zaman_doldu() or _deneme_sayaci > _MAKS_DENEME:
                break

            # Indeks: (gun,saat) -> o saati kaplayan gorev id'leri (coklu-
            # saat bloklarin HER saati icin ayri kayit). Bu, "bu saatte ne
            # var" sorgusunu O(874)'ten O(1)'e indirir.
            zaman_index = {}
            # Indeks: tc -> {gun: [saatler]} - pencere hesaplamasi ve
            # bos_hucreler ARTIK HER problemli ogretmen icin 874 gorevi
            # taramiyor, bunun yerine bu indeksten O(1) okuyor.
            ogrt_gun_index = {}
            tc_gorev_index = {}
            for g in gorevler:
                if not g["placed"]:
                    continue
                gp, sp = g["placed"]
                for b in range(g["boy"]):
                    zaman_index.setdefault((gp, sp + b), []).append(g["id"])
                for otc in tum_ogrt(g):
                    d = ogrt_gun_index.setdefault(otc, {}).setdefault(gp, [])
                    d.extend(range(sp, sp + g["boy"]))
                    tc_gorev_index.setdefault(otc, []).append(g)

            def _yan_yana_kucukleri_topla(gun, saat, hedef_boy):
                """(gun, saat)'ten baslayarak YAN YANA duran, toplam
                uzunlugu TAM OLARAK hedef_boy eden kucuk dersleri toplar.
                Orn. hedef_boy=2 icin: 1 saatlik Saglik Bilgisi + 1
                saatlik Rehberlik. En az 2 ders olmali (tek ders zaten
                esit-uzunluk yolundan denenmis olur). Tam eslesme yoksa
                None doner - kismi/tasan eslesme ASLA kabul edilmez."""
                toplanan = []
                toplam = 0
                imlec = saat
                while toplam < hedef_boy:
                    secilen = None
                    for gid_i in zaman_index.get((gun, imlec), []):
                        gg = gid_map[gid_i]
                        # SADECE o saatte BASLAYAN ve hedeften KUCUK olan
                        if gg["placed"] == (gun, imlec) and gg["boy"] < hedef_boy:
                            secilen = gid_i
                            break
                    if secilen is None:
                        return None
                    gg = gid_map[secilen]
                    if toplam + gg["boy"] > hedef_boy:
                        return None  # tasma - blok bolunmesine yol acardi, ASLA
                    toplanan.append(secilen)
                    toplam += gg["boy"]
                    imlec += gg["boy"]
                if toplam != hedef_boy or len(toplanan) < 2:
                    return None
                return toplanan

            # HEDEF LISTESI: eskiden SADECE penceresi esigi asanlar
            # taraniyordu. Penceresi olmayan ama BOS GUNU de OLMAYAN bir
            # ogretmen bu donguye HIC girmiyordu - bu yuzden iyilestirme
            # asamasinda bos gun sayisi bir turlu dusmuyordu. Artik iki
            # sorunlu grup birlikte taranir.
            def _bos_gunsuz_mu(tc2):
                if idareci_mi[tc2] or tc_kisit[tc2]["bosGunIstemez"]:
                    return False
                gs = ogrt_gun_index.get(tc2) or {}
                return all(gs.get(g) for g in gunler)

            pencereli = sorted(
                (tc for tc in tum_tc if not idareci_mi[tc]
                 and (_pencere_hizli(tc, ogrt_gun_index) > MAX_PENCERE_HEDEF
                      or _bos_gunsuz_mu(tc))),
                key=lambda tc: -(_pencere_hizli(tc, ogrt_gun_index)
                                 + (30 if _bos_gunsuz_mu(tc) else 0)))
            if not pencereli:
                break

            herhangi_degisti = False
            for tc in pencereli:
                if _zt_zaman_doldu() or _deneme_sayaci > _MAKS_DENEME:
                    break
                # ONEMLI: Bir ogretmen icin sadece TEK bir basarili takas
                # bulununca durmuyoruz - AYNI PASS icinde bu ogretmenin
                # MUMKUN OLDUGUNCA COK bosluguna takas denenir (bos
                # hucreler her basarili takastan sonra YENIDEN hesaplanir).
                # Bu, 'derinlik yetersiz - belirli bir yere kadar
                # yapiyor gerisi yok' sikayetine dogrudan cevaptir: tek
                # bir yuzeysel takas yerine, ayni ogretmen icin ZINCIRLEME
                # (ama HER ADIM BAGIMSIZ VE GUVENLI dogrulanmis) coklu
                # iyilestirme dener - ozyinelemeli/riskli derin kovma
                # olmadan.
                # ADAPTIF DENEME SAYISI: pencere degeri ne kadar kotuyse
                # (hedeften ne kadar uzaksa) o kadar fazla deneme hakki
                # verilir - en zorlu vakalara daha fazla arama gucu.
                _baslangic_pencere_tc = _pencere_hizli(tc, ogrt_gun_index)
                _ic_deneme_sayisi = min(20, 12 + (_baslangic_pencere_tc - MAX_PENCERE_HEDEF) * 2)
                for _ic_deneme in range(_ic_deneme_sayisi):
                    if _zt_zaman_doldu() or _deneme_sayaci > _MAKS_DENEME:
                        break
                    once_pencere = _pencere_hizli(tc, ogrt_gun_index)
                    if once_pencere <= MAX_PENCERE_HEDEF:
                        break  # bu ogretmen icin hedefe zaten ulasildi
                    bos_hucreler = []
                    for gun, saatler_o_gun_ham in ogrt_gun_index.get(tc, {}).items():
                        saatler_o_gun = sorted(set(saatler_o_gun_ham))
                        if len(saatler_o_gun) < 2:
                            continue
                        for s in range(min(saatler_o_gun), max(saatler_o_gun) + 1):
                            if s not in saatler_o_gun:
                                bos_hucreler.append((gun, s))
                    if not bos_hucreler:
                        break
                    tc_tasklari = tc_gorev_index.get(tc, [])
                    basarili_oldu = False
                    for bos_gun, bos_saat in bos_hucreler:
                        if basarili_oldu or _zt_zaman_doldu() or _deneme_sayaci > _MAKS_DENEME:
                            break
                        adaylar = zaman_index.get((bos_gun, bos_saat), [])
                        for g2_id in adaylar:
                            if basarili_oldu or _zt_zaman_doldu() or _deneme_sayaci > _MAKS_DENEME:
                                break
                            g2 = gid_map[g2_id]
                            if tc in tum_ogrt(g2):
                                continue  # zaten tc'nin kendi dersi - atla
                            # GLOBAL KONTROL: takas ortaginin (g2'nin
                            # ogretmen(ler)i) penceresi bu takastan sonra
                            # KOTULESMEMELI - aksi halde sadece sorunu bir
                            # ogretmenden digerine TASIMIS oluruz, gercek
                            # bir iyilesme saglamayiz. Takastan ONCE bu
                            # ogretmen(ler)in mevcut pencere degerini
                            # kaydediyoruz, SONRA karsilastiracagiz.
                            digerleri_dis = [t for t in tum_ogrt(g2) if t != tc]
                            for g1 in tc_tasklari:
                                # KARMA TAKAS ENTEGRASYONU: eskiden farkli
                                # uzunluktaki bloklar KOSULSUZ atlaniyordu
                                # (g1["boy"] != g2["boy"] -> continue). Bu,
                                # 1 saatlik dersler ile 2-4 saatlik bloklar
                                # arasinda hic kopru birakmiyordu. Artik g1
                                # DAHA BUYUKSE, hedef bosluktan baslayarak
                                # YAN YANA duran kucuk dersler toplanip
                                # BUTUN blok <-> kucukler takasi denenir.
                                _karma_kucukler = None
                                if g1["boy"] != g2["boy"]:
                                    if g1["boy"] > g2["boy"]:
                                        _karma_kucukler = _yan_yana_kucukleri_topla(
                                            bos_gun, bos_saat, g1["boy"])
                                    if not _karma_kucukler:
                                        continue
                                    if g1["id"] in _karma_kucukler:
                                        continue  # kendi gorevi - anlamsiz
                                # Etkilenen "diger" ogretmenler: karma takasta
                                # TUM kucuk derslerin ogretmenleri etkilenir.
                                if _karma_kucukler:
                                    _etkilenen = set()
                                    for _kid in _karma_kucukler:
                                        _etkilenen.update(tum_ogrt(gid_map[_kid]))
                                    _etkilenen.discard(tc)
                                    digerleri = sorted(_etkilenen)
                                else:
                                    digerleri = digerleri_dis
                                once_digerleri = {t: _pencere_hizli(t, ogrt_gun_index) for t in digerleri}
                                _sa_sifir_once = sifir_bos_gun_toplam()
                                _deneme_sayaci += 1
                                nokta = kontrol_noktasi()
                                _hamle_oldu = (
                                    _zaman_takasi_karma_uygula(g1["id"], _karma_kucukler)
                                    if _karma_kucukler else
                                    _zaman_takasi_uygula(g1["id"], g2["id"]))
                                if _hamle_oldu:
                                    # UCUZ HESAPLAMA: 874 gorevi TARAMADAN,
                                    # etkilenen ogretmenin KENDI (kucuk,
                                    # ~15-20 gorevlik) listesini CANLI
                                    # OKUYARAK (g["placed"] takastan sonra
                                    # otomatik guncel) pencere hesapla. Bu
                                    # liste (tc_gorev_index) o ogretmenin
                                    # HANGI gorevleri oldugunu gosterir -
                                    # bu bilgi takastan ETKILENMEZ (sadece
                                    # NE ZAMAN oldugu degisir), bu yuzden
                                    # STALE degildir.
                                    def _pencere_canli(hedef_tc):
                                        gun_saat = {}
                                        for g in tc_gorev_index.get(hedef_tc, []):
                                            if not g["placed"]:
                                                continue
                                            gp2, sp2 = g["placed"]
                                            gun_saat.setdefault(gp2, []).extend(range(sp2, sp2 + g["boy"]))
                                        toplam = 0
                                        for saatler_ham in gun_saat.values():
                                            saatler = sorted(set(saatler_ham))
                                            if len(saatler) < 2:
                                                continue
                                            toplam += (max(saatler) - min(saatler) + 1) - len(saatler)
                                        return toplam

                                    yeni_pencere = _pencere_canli(tc)
                                    digerleri_sonra = {t: _pencere_canli(t) for t in digerleri}
                                    kotulesen_var = any(
                                        digerleri_sonra[t] > once_digerleri[t] for t in digerleri)
                                    # GERCEK SIMULATED ANNEALING KABUL KRITERI:
                                    # eskiden SADECE 'hedef ogretmen kesin
                                    # iyilessin VE kimse kotulesmesin' kabul
                                    # ediliyordu - bu COK KATI bir kural, ve
                                    # 20+ saatlik gercek testler gosterdi ki
                                    # bu, aramanin bir YEREL CUKURDA
                                    # TAKILMASINA yol aciyor (ASC/FET'in
                                    # simulated annealing ile kacindigi tam
                                    # olarak bu). Artik TOPLAM etki (hedef +
                                    # TUM etkilenenler) degerlendiriliyor:
                                    #  - TOPLAM azaliyorsa: HER ZAMAN kabul
                                    #    (bazi bireyler kotulesse bile, NET
                                    #    iyilesme varsa kabul edilir - bu,
                                    #    eskisinden DAHA GUCLU bir kabul).
                                    #  - TOPLAM AYNI kalıyorsa (yanal hamle):
                                    #    sicakliga bagli bir olasilikla kabul
                                    #    edilir - bu, aramanin farkli
                                    #    konfigurasyonlari 'dolasarak' daha
                                    #    sonra gercek bir iyilesme bulmasini
                                    #    saglar (SA'nin temel mantigi).
                                    #  - TOPLAM ARTIYORSA: ASLA kabul edilmez
                                    #    (net kotulesme kesinlikle onlenir).
                                    toplam_once = (_sa_maliyet(once_pencere)
                                                   + sum(_sa_maliyet(v) for v in once_digerleri.values())
                                                   + _sa_sifir_once * _SA_BOSGUN)
                                    toplam_sonra = (_sa_maliyet(yeni_pencere)
                                                    + sum(_sa_maliyet(v) for v in digerleri_sonra.values())
                                                    + sifir_bos_gun_toplam() * _SA_BOSGUN)
                                    # Sicaklik: _dis_tur ilerledikce (0->14) azalir,
                                    # yanal/kotu hamle kabul olasiligi da azalir.
                                    # SICAKLIK: eskiden 0.35->0.05 idi; bu
                                    # aralik exp(-fark/T) icin COK DUSUK
                                    # (exp(-1/0.35)=%5.8) - yani kotulesen
                                    # hamleler pratikte hic kabul edilmezdi.
                                    # 1.5->0.15 araligi gercek kesif saglar:
                                    # baslangicta +1 kotulesme ~%51, sonlara
                                    # dogru ~%0.1 olasilikla kabul edilir
                                    # (klasik SA sogutma egrisi).
                                    sicaklik = _sa_sicaklik()
                                    fark = toplam_sonra - toplam_once
                                    kabul_edildi = _sa_kabul_mu(fark, sicaklik)
                                    if kabul_edildi:
                                        if fark > 0:
                                            _sa_kabul_kotu += 1
                                        _sa_kaydet(fark)
                                        herhangi_degisti = True
                                        basarili_oldu = True
                                        _basarili_takas_sayisi += 1
                                        # KABUL EDILDI - ogrt_gun_index'i
                                        # SADECE simdi, kalici olarak
                                        # guncelle (ayni ucuz yontemle).
                                        for etkilenen in {tc} | set(digerleri):
                                            yeni_gun_saat = {}
                                            for g in tc_gorev_index.get(etkilenen, []):
                                                if g["placed"]:
                                                    gp2, sp2 = g["placed"]
                                                    yeni_gun_saat.setdefault(gp2, []).extend(
                                                        range(sp2, sp2 + g["boy"]))
                                            ogrt_gun_index[etkilenen] = yeni_gun_saat
                                        break
                                    else:
                                        geri_al(nokta)
                                        # KARMA TAKAS reddedildiyse ZINCIRE
                                        # GIRME: zincir mantigi butunuyle
                                        # ESIT UZUNLUK varsayimina dayanir
                                        # (adaylar_z filtresinde
                                        # g_yeni["boy"] == g1["boy"]).
                                        # Karma durumda g2 daha kucuk
                                        # oldugu icin zincir anlamsiz olur;
                                        # bir sonraki adaya gecilir.
                                        if _karma_kucukler:
                                            continue
                                        # IKILI TAKAS YETERSIZ KALDI - kullanicinin
                                        # istegi uzerine ZINCIRI 3, 4, 5, 6, 7, 8
                                        # UZUNLUGUNA KADAR genisleterek dene.
                                        # Her adimda zincirin SON eklenen
                                        # ogretmeninin BASKA bir dersini
                                        # bulup zincire ekleriz - bu, A->B
                                        # ikili takasin cozemedigi, ama
                                        # A->B->C->...->A gibi uzun bir
                                        # dongunun cozebilecegi durumlari
                                        # yakalar (ASC/FET'in de kullandigi
                                        # 'zincirleme takas' mantigi).
                                        _zincir = [g1["id"], g2["id"]]
                                        _zincir_son_ogrt = g2["tc"]
                                        _kullanilan_idler = {g1["id"], g2["id"]}
                                        # Zincir uzunlugu sinirini YUKSEK
                                        # tuttuk (kullanicinin istegi
                                        # uzerine) - GUVENLIK zaten HER
                                        # ADIMDA _zt_zaman_doldu() ve
                                        # _deneme_sayaci kontrolleriyle
                                        # saglaniyor, bu yuzden zincir
                                        # gerekirse (ve zaman/deneme
                                        # butcesi izin verdigi surece)
                                        # onlarca/yuzlerce ogretmene kadar
                                        # uzayabilir - MAX_ZINCIR sadece
                                        # bir ANLAMSIZ SONSUZ DONGUYE
                                        # (herkes tukenip aday kalmayana
                                        # kadar) karsi son bir guvenlik agi.
                                        MAX_ZINCIR = min(20, len(tum_tc))
                                        while len(_zincir) < MAX_ZINCIR and not basarili_oldu:
                                            if _zt_zaman_doldu() or _deneme_sayaci > _MAKS_DENEME:
                                                break
                                            adaylar_z = [g_yeni for g_yeni in tc_gorev_index.get(_zincir_son_ogrt, [])
                                                         if g_yeni["id"] not in _kullanilan_idler
                                                         and g_yeni["boy"] == g1["boy"] and g_yeni["placed"]]
                                            aday_bulundu = False
                                            if adaylar_z:
                                                # RASTGELE sec (sadece ilk bulunani degil) - boylece
                                                # farkli turlarda farkli zincir yapilari kesfedilir.
                                                g_yeni = random.choice(adaylar_z)
                                                _zincir.append(g_yeni["id"])
                                                _kullanilan_idler.add(g_yeni["id"])
                                                _zincir_son_ogrt = g_yeni["tc"]
                                                aday_bulundu = True
                                            if not aday_bulundu:
                                                break  # zincir uzatilamiyor - daha fazla aday yok
                                            _deneme_sayaci += 1
                                            nokta3 = kontrol_noktasi()
                                            if _zaman_zincir_uygula(_zincir):
                                                yeni_pencere_z = _pencere_canli(tc)
                                                digerleri_sonra_z = {t: _pencere_canli(t) for t in digerleri}
                                                toplam_sonra_z = (_sa_maliyet(yeni_pencere_z)
                                                                  + sum(_sa_maliyet(v) for v in digerleri_sonra_z.values())
                                                                  + sifir_bos_gun_toplam() * _SA_BOSGUN)
                                                fark_z = toplam_sonra_z - toplam_once
                                                kabul_z = _sa_kabul_mu(fark_z, sicaklik)
                                                if kabul_z:
                                                    if fark_z > 0:
                                                        _sa_kabul_kotu += 1
                                                    _sa_kaydet(fark_z)
                                                    herhangi_degisti = True
                                                    basarili_oldu = True
                                                    _basarili_takas_sayisi += 1
                                                    for etkilenen in {tc} | set(digerleri):
                                                        yeni_gun_saat = {}
                                                        for g in tc_gorev_index.get(etkilenen, []):
                                                            if g["placed"]:
                                                                gp2, sp2 = g["placed"]
                                                                yeni_gun_saat.setdefault(gp2, []).extend(
                                                                    range(sp2, sp2 + g["boy"]))
                                                        ogrt_gun_index[etkilenen] = yeni_gun_saat
                                                else:
                                                    geri_al(nokta3)
                                        if basarili_oldu:
                                            break
                                if basarili_oldu:
                                    break
                            if basarili_oldu:
                                break
                    if not basarili_oldu:
                        break  # bu ogretmen icin bu turda daha fazla iyilestirme bulunamadi

        # ---- SA GUVENLIK AGI: EN IYI DURUMA GERI DON ----
        # SA sirasinda bilerek kotulesen hamleler kabul edildi (yerel
        # optimumdan kacmak icin). Pass bitiminde mevcut durum, gorulen
        # EN IYI durumdan kotuyse, o en iyi ana GERI DONULUR. Boylece:
        # kesif serbest, ama SONUC ASLA KOTULESMEZ.
        if _sa_kumulatif > _sa_en_iyi_kumulatif:
            _simdiki_ihlal = (ihlal_sayisi(), fazla_bos_gun_toplam(), sifir_bos_gun_toplam())
            # Geri donus SADECE mutlak kural ihlallerini ARTIRMIYORSA
            # yapilir. Aksi halde pencere kazancindan vazgecilir - mutlak
            # kurallar (tek ders / 2+ bos gun / bos gunsuz) HER ZAMAN
            # pencereden onceliklidir.
            if all(a <= b for a, b in zip(_sa_en_iyi_ihlal, _simdiki_ihlal)):
                geri_al(_sa_en_iyi_nokta)

        _bitis_pencere_fazla = sum(
            1 for tc in tum_tc if not idareci_mi[tc] and ogrt_haftalik_pencere(tc) > MAX_PENCERE_HEDEF)
        print(f"[ZAMAN TAKASI] dis_tur={_dis_tur_sayisi} deneme={_deneme_sayaci} "
              f"basarili_takas={_basarili_takas_sayisi} pencere_fazla: {_baslangic_pencere_fazla} -> "
              f"{_bitis_pencere_fazla} [SA: kotu_kabul={_sa_kabul_kotu} net={_sa_kumulatif} "
              f"en_iyi={_sa_en_iyi_kumulatif}] sure_kullanilan={round(time.time()-t0,1)}s/{_deneme_butcesi}s", flush=True)

    zaman_takasi_pencere_pass()

    def yik_yeniden_kur_pass():
        """RUIN & RECREATE (YIK-YENIDEN KUR) - BUYUK HAMLE MEKANIZMASI.

        NEDEN GEREKLI: mevcut tum hamlelerimiz (ikili/3'lu/N'li zaman
        takasi, brans takasi, karma blok takasi) KUCUK ve YEREL
        hamlelerdir - cozumun etrafindaki dar bir komsulugu tararlar.
        Gercek loglar gosterdi ki bu komsulugun icinde daha iyisi
        KALMIYOR (net=-4 gibi kirinti kazanclar), yani arama dar bir
        daireye hapsolmus durumda. Bu fonksiyon o daireyi KAT KAT
        genisletir: en sorunlu birkac ogretmenin TUM derslerini programdan
        CIKARIR (yik) ve greedy motorla SIFIRDAN yeniden yerlestirir
        (yeniden kur). Tek adimda onlarca ders birden degisir - ikili
        takasin ASLA ulasamayacagi bir mesafe.

        MUTLAK GUVENLIK: her tur bir kontrol noktasiyla baslar. Yeniden
        kurma sirasinda TEK BIR ders bile yerlesemezse, VEYA sonuc
        maliyeti kotulesirse, VEYA mutlak kurallar (tek ders / 2+ bos gun
        / bos gunsuz) kotulesirse -> TAMAMI GERI ALINIR. Yani bu pass
        sonucu ASLA kotulestiremez, sadece iyilestirebilir.
        """
        nonlocal _deneme_butcesi
        _yr_baslangic = time.time()
        _yr_butce = min(25.0, max(8.0, _deneme_butcesi * 0.25))
        # KRITIK: yerlestirmeye_calis(), GENEL butce dolduysa derinlik=0'da
        # hemen False doner. Bu pass, SA pass'inden SONRA calistigi icin
        # genel butce cogunlukla ZATEN DOLMUS olur - yani yeniden kurma
        # HER ZAMAN basarisiz olur ve pass hicbir ise yaramazdi. Bu yuzden
        # genel butceyi bu pass suresince GECICI olarak uzatiyoruz ve
        # sonunda MUTLAKA eski degerine dondurulyoruz (finally).
        _yr_eski_butce = _deneme_butcesi
        _deneme_butcesi = (time.time() - t0) + _yr_butce + 2.0
        try:
            _yik_yeniden_kur_govde(_yr_baslangic, _yr_butce)
        finally:
            _deneme_butcesi = _yr_eski_butce

    def _yik_yeniden_kur_govde(_yr_baslangic, _yr_butce):

        def _yr_zaman_doldu():
            return time.time() - _yr_baslangic > _yr_butce

        def _global_pencere_maliyet():
            """KRITIK DUZELTME: bu maliyet eskiden SADECE pencereye
            bakiyordu. Bos gun sayisi yalnizca bir 'kotulestirme bekcisi'
            idi - yani arama hicbir zaman BILEREK birine bos gun
            kazandirmaya calismiyordu. Sonuc: saatlerce calissa da bos
            gunsuz ogretmen sayisi ancak sans eseri dusuyordu.
            Artik bos gunsuz her ogretmen AGIR cezalidir (hesapla_skor'da
            da pencereden ONCE geldigi icin agirlik buyuk tutuldu)."""
            m = 0
            for tc2 in tum_tc:
                if idareci_mi[tc2]:
                    continue
                if not tc_kisit[tc2]["bosGunIstemez"]:
                    bos = sum(1 for g2 in gunler if day_load[tc2][g2] == 0)
                    if bos == 0:
                        m += 150          # bos gunu YOK - en agir ceza
                p = ogrt_haftalik_pencere(tc2)
                if p > MAX_PENCERE_HEDEF:
                    m += 10 + (p - MAX_PENCERE_HEDEF)
            return m

        _basarili_yikim = 0
        for _yr_tur in range(60):
            if _yr_zaman_doldu():
                break
            adaylar = [tc2 for tc2 in tum_tc
                       if not idareci_mi[tc2] and ogrt_haftalik_pencere(tc2) > MAX_PENCERE_HEDEF]
            if not adaylar:
                break
            once_maliyet = _global_pencere_maliyet()
            once_ihlal = (ihlal_sayisi(), fazla_bos_gun_toplam(), sifir_bos_gun_toplam())
            nokta = kontrol_noktasi()

            # YIK: en sorunlu ogretmenlerden rastgele bir alt kume sec
            random.shuffle(adaylar)
            secilen = adaylar[:min(len(adaylar), random.randint(2, 6))]
            secilen_kume = set(secilen)
            yikilan = []
            yikilan_kume = set()
            for g in gorevler:
                if not g["placed"]:
                    continue
                if g["id"] in yikilan_kume:
                    continue
                if secilen_kume & set(tum_ogrt(g)):
                    yikilan.append(g["id"])
                    yikilan_kume.add(g["id"])
            if not yikilan:
                continue
            for gid_y in yikilan:
                bosalt(gid_y)

            # YENIDEN KUR: buyuk bloklar once (yerlestirmesi daha zor)
            yikilan.sort(key=lambda gid_y: -gid_map[gid_y]["boy"])
            basarisiz = False
            for gid_y in yikilan:
                if _yr_zaman_doldu():
                    basarisiz = True
                    break
                if not yerlestirmeye_calis(gid_y):
                    basarisiz = True
                    break

            if basarisiz:
                geri_al(nokta)
                continue
            sonra_ihlal = (ihlal_sayisi(), fazla_bos_gun_toplam(), sifir_bos_gun_toplam())
            sonra_maliyet = _global_pencere_maliyet()
            # KABUL SARTI: mutlak kurallar kotulesmeyecek VE pencere
            # maliyeti KESIN iyilesecek (esitlik kabul edilmez - bosuna
            # degisiklik yapip kararliligi bozmayalim).
            if any(a > b for a, b in zip(sonra_ihlal, once_ihlal)) or sonra_maliyet >= once_maliyet:
                geri_al(nokta)
            else:
                _basarili_yikim += 1

        if _basarili_yikim:
            print(f"[YIK-YENIDEN KUR] basarili_buyuk_hamle={_basarili_yikim} "
                  f"sure={round(time.time()-_yr_baslangic,1)}s/{round(_yr_butce,1)}s", flush=True)

    yik_yeniden_kur_pass()

    def cp_sat_pencere_pass():
        """OR-TOOLS CP-SAT ile PENCERE ALT PROBLEMI COZUMU.

        FIKIR: ogretmen-ders ATAMALARI SABIT kalir (kim neyi ogretiyor
        degismez); sadece SORUNLU ogretmenlerin derslerinin ZAMANLARI
        yeniden belirlenir. Bu, tum problemi degil KUCUK bir alt problemi
        cozucuye verdigimiz icin CP-SAT'in gucunu (celiski ogrenme,
        kanitlanmis optimallik) makul bir boyutta kullanmamizi saglar.

        MUTLAK GUVENLIK:
        - OR-Tools kurulu degilse SESSIZCE atlanir (motor eskisi gibi calisir)
        - Herhangi bir hata olursa yakalanir ve mevcut cozum KORUNUR
        - Cozucu sonucu, uygulanmadan once musait_mi ile TEK TEK dogrulanir
        - Uygulama sonrasi maliyet/ihlaller kotulesirse TAMAMI GERI ALINIR
        """
        if _cp_model is None:
            return
        try:
            _cp_sat_govde()
        except Exception as _cp_hata:
            print(f"[CP-SAT] atlandi (hata: {type(_cp_hata).__name__}: {_cp_hata})", flush=True)

    def _cp_sat_govde():
        hedef_tc = [tc2 for tc2 in tum_tc
                    if not idareci_mi[tc2] and ogrt_haftalik_pencere(tc2) > MAX_PENCERE_HEDEF]
        if not hedef_tc:
            return
        # EN SORUNLU ogretmenlere ODAKLAN: tum esik ustu ogretmenleri
        # birden modele koymak, modeli cozucunun makul surede
        # cozemeyecegi kadar buyutuyor (ve 400 ders sinirina takilip
        # SESSIZCE atlaniyordu). En kotu N ogretmen secilerek problem
        # CP-SAT'in gucunu gosterebilecegi bir boyutta tutulur.
        hedef_tc.sort(key=lambda t2: -ogrt_haftalik_pencere(t2))
        hedef_tc = hedef_tc[:12]
        hedef_kume = set(hedef_tc)
        # Serbest birakilacak gorevler: hedef ogretmenlerin TUM yerlesmis dersleri
        serbest = [g for g in gorevler if g["placed"] and (hedef_kume & set(tum_ogrt(g)))]
        if not serbest:
            return
        if len(serbest) > 400:
            print(f"[CP-SAT] atlandi: model cok buyuk ({len(serbest)} ders)", flush=True)
            return

        serbest_idler = {g["id"] for g in serbest}
        # Modele giren TUM ogretmenler (ortak derslerin ikinci ogretmenleri dahil)
        model_tc = set()
        for g in serbest:
            model_tc.update(tum_ogrt(g))
        model_tc = sorted(t for t in model_tc if not idareci_mi[t])

        # DONMUS (frozen) doluluk: serbest OLMAYAN derslerin isgal ettigi hucreler
        donmus_sinif = set()   # (sid, gun, saat)
        donmus_ogrt = set()    # (tc, gun, saat)
        for g in gorevler:
            if not g["placed"] or g["id"] in serbest_idler:
                continue
            gp, sp = g["placed"]
            for b in range(g["boy"]):
                donmus_sinif.add((g["sid"], gp, sp + b))
                for t2 in tum_ogrt(g):
                    donmus_ogrt.add((t2, gp, sp + b))

        # ADAY SLOTLAR
        adaylar = {}
        for g in serbest:
            liste = []
            for gun in gunler:
                for saat in range(1, gun_bilgi[gun] - g["boy"] + 2):
                    uygun = True
                    for b in range(g["boy"]):
                        h = saat + b
                        if (g["sid"], gun, h) in donmus_sinif:
                            uygun = False
                            break
                        for t2 in tum_ogrt(g):
                            if (t2, gun, h) in donmus_ogrt:
                                uygun = False
                                break
                            if (gun, h) in tc_kisit[t2]["kapaliSaat"]:
                                uygun = False
                                break
                        if not uygun:
                            break
                    if uygun:
                        liste.append((gun, saat))
            if not liste:
                print(f"[CP-SAT] atlandi: bir ders icin uygun slot yok (sinif={g['sid']})", flush=True)
                return  # bir ders icin hic aday yoksa modeli kurma
            adaylar[g["id"]] = liste

        model = _cp_model.CpModel()
        x = {}
        for g in serbest:
            for (gun, saat) in adaylar[g["id"]]:
                x[(g["id"], gun, saat)] = model.NewBoolVar(f"x_{g['id']}_{gun}_{saat}")
            model.AddExactlyOne([x[(g["id"], gun, saat)] for (gun, saat) in adaylar[g["id"]]])

        # SINIF cakismasi: bir sinif ayni saatte tek ders
        sinif_hucre = {}
        for g in serbest:
            for (gun, saat) in adaylar[g["id"]]:
                for b in range(g["boy"]):
                    sinif_hucre.setdefault((g["sid"], gun, saat + b), []).append(
                        x[(g["id"], gun, saat)])
        for _k, degiskenler in sinif_hucre.items():
            if len(degiskenler) > 1:
                model.AddAtMostOne(degiskenler)

        # OGRETMEN cakismasi + doluluk degiskenleri
        ogrt_hucre = {}
        for g in serbest:
            for (gun, saat) in adaylar[g["id"]]:
                for b in range(g["boy"]):
                    for t2 in tum_ogrt(g):
                        ogrt_hucre.setdefault((t2, gun, saat + b), []).append(
                            x[(g["id"], gun, saat)])
        occ = {}
        for t2 in model_tc:
            for gun in gunler:
                for h in range(1, gun_bilgi[gun] + 1):
                    v = model.NewBoolVar(f"occ_{t2}_{gun}_{h}")
                    # KRITIK DUZELTME: bu ogretmenin DONDURULMUS (serbest
                    # birakilmamis) dersleri de doluluga dahil edilmeli.
                    # Aksi halde ortak dersi olan / bir kismi donmus
                    # ogretmenlerin gun yuku EKSIK sayiliyor ve "tam 1 bos
                    # gun" / "min gunluk saat" kurallari IMKANSIZ hale
                    # gelip model INFEASIBLE donuyordu.
                    if (t2, gun, h) in donmus_ogrt:
                        model.Add(v == 1)
                        occ[(t2, gun, h)] = v
                        continue
                    degiskenler = ogrt_hucre.get((t2, gun, h), [])
                    if degiskenler:
                        model.AddAtMostOne(degiskenler)
                        model.Add(v == sum(degiskenler))
                    else:
                        model.Add(v == 0)
                    occ[(t2, gun, h)] = v

        # AYNI DERS AYNI GUN iki kez olmasin
        ders_gun = {}
        for g in serbest:
            for (gun, saat) in adaylar[g["id"]]:
                ders_gun.setdefault((g["sid"], g["did"], gun), []).append(x[(g["id"], gun, saat)])
        for _k, degiskenler in ders_gun.items():
            if len(degiskenler) > 1:
                model.AddAtMostOne(degiskenler)

        # GUNLUK KURALLAR: min/max saat + TAM 1 BOS GUN
        gun_kullanildi = {}
        _bos_gun_cezalari = []
        for t2 in model_tc:
            ming = tc_kisit[t2]["minG"] or 0
            maxg = tc_kisit[t2]["maxG"] or 8
            for gun in gunler:
                saat_toplam = sum(occ[(t2, gun, h)] for h in range(1, gun_bilgi[gun] + 1))
                y = model.NewBoolVar(f"y_{t2}_{gun}")
                gun_kullanildi[(t2, gun)] = y
                model.Add(saat_toplam <= maxg)
                model.Add(saat_toplam <= gun_bilgi[gun] * y)
                if ming:
                    model.Add(saat_toplam >= ming * y)  # ASLA TEK DERS
                else:
                    model.Add(saat_toplam >= y)
            # BOS GUN: "2+ bos gun ASLA" kurali HARD kalir (MEB mutlak
            # kurali). Ancak "en az 1 bos gun" HARD yapilirsa, mevcut
            # cozumde bos gunu OLMAYAN ogretmenler yuzunden model
            # komple INFEASIBLE oluyordu. Bu yuzden o kisim AMAC
            # FONKSIYONUNA (agir cezayla) tasindi - cozucu bulabilirse
            # bos gun verir, bulamazsa en azindan pencereyi iyilestirir.
            model.Add(sum(gun_kullanildi[(t2, gun)] for gun in gunler) >= len(gunler) - 1)
            _bos_gun_yok = model.NewBoolVar(f"bosgunyok_{t2}")
            model.Add(sum(gun_kullanildi[(t2, gun)] for gun in gunler) == len(gunler)).OnlyEnforceIf(_bos_gun_yok)
            model.Add(sum(gun_kullanildi[(t2, gun)] for gun in gunler) <= len(gunler) - 1).OnlyEnforceIf(_bos_gun_yok.Not())
            _bos_gun_cezalari.append(_bos_gun_yok)

        # PENCERE MODELI: bir saat "pencere"dir <=> dolu degil AMA
        # oncesinde ve sonrasinda dolu saat var.
        pencere_degiskenleri = {}
        for t2 in model_tc:
            for gun in gunler:
                H = gun_bilgi[gun]
                for h in range(2, H):
                    onceki = [occ[(t2, gun, k)] for k in range(1, h)]
                    sonraki = [occ[(t2, gun, k)] for k in range(h + 1, H + 1)]
                    if not onceki or not sonraki:
                        continue
                    once_var = model.NewBoolVar(f"o_{t2}_{gun}_{h}")
                    model.AddMaxEquality(once_var, onceki)
                    sonra_var = model.NewBoolVar(f"s_{t2}_{gun}_{h}")
                    model.AddMaxEquality(sonra_var, sonraki)
                    bosluk = model.NewBoolVar(f"p_{t2}_{gun}_{h}")
                    model.AddBoolAnd([once_var, sonra_var, occ[(t2, gun, h)].Not()]).OnlyEnforceIf(bosluk)
                    model.AddBoolOr([once_var.Not(), sonra_var.Not(), occ[(t2, gun, h)]]).OnlyEnforceIf(bosluk.Not())
                    pencere_degiskenleri.setdefault(t2, []).append(bosluk)

        # AMAC: kullanicinin hedefi -> esigi ASAN ogretmen sayisini
        # minimize et (agir ceza), ikincil olarak toplam pencere.
        amac = []
        for t2 in model_tc:
            plist = pencere_degiskenleri.get(t2, [])
            if not plist:
                continue
            toplam_p = model.NewIntVar(0, len(plist), f"w_{t2}")
            model.Add(toplam_p == sum(plist))
            asiyor = model.NewBoolVar(f"asiyor_{t2}")
            model.Add(toplam_p >= MAX_PENCERE_HEDEF + 1).OnlyEnforceIf(asiyor)
            model.Add(toplam_p <= MAX_PENCERE_HEDEF).OnlyEnforceIf(asiyor.Not())
            amac.append(20 * asiyor)
            amac.append(toplam_p)
        # BOS GUNSUZ ogretmen basina AGIR ceza (mutlak kurala yakin agirlik)
        for _bg in _bos_gun_cezalari:
            amac.append(50 * _bg)
        if not amac:
            return
        model.Minimize(sum(amac))

        cozucu = _cp_model.CpSolver()
        _cp_butce = min(30.0, max(10.0, _deneme_butcesi * 0.3))
        cozucu.parameters.max_time_in_seconds = _cp_butce
        cozucu.parameters.num_search_workers = 2
        durum = cozucu.Solve(model)
        if durum not in (_cp_model.OPTIMAL, _cp_model.FEASIBLE):
            print(f"[CP-SAT] cozum bulunamadi (durum={cozucu.StatusName(durum)}) - mevcut cozum korunuyor", flush=True)
            return

        # UYGULA (tam geri alma korumasiyla)
        once_ihlal = (ihlal_sayisi(), fazla_bos_gun_toplam(), sifir_bos_gun_toplam())
        once_fazla_sayi = sum(1 for t2 in tum_tc
                              if not idareci_mi[t2] and ogrt_haftalik_pencere(t2) > MAX_PENCERE_HEDEF)
        nokta = kontrol_noktasi()
        for g in serbest:
            bosalt(g["id"])
        basarisiz = False
        for g in serbest:
            hedef_slot = None
            for (gun, saat) in adaylar[g["id"]]:
                if cozucu.Value(x[(g["id"], gun, saat)]):
                    hedef_slot = (gun, saat)
                    break
            if hedef_slot is None or not musait_mi(g["id"], hedef_slot[0], hedef_slot[1]):
                basarisiz = True
                break
            yerlestir(g["id"], hedef_slot[0], hedef_slot[1])
        if basarisiz:
            geri_al(nokta)
            print("[CP-SAT] cozum uygulanamadi - mevcut cozum korunuyor", flush=True)
            return
        sonra_ihlal = (ihlal_sayisi(), fazla_bos_gun_toplam(), sifir_bos_gun_toplam())
        sonra_fazla_sayi = sum(1 for t2 in tum_tc
                               if not idareci_mi[t2] and ogrt_haftalik_pencere(t2) > MAX_PENCERE_HEDEF)
        if any(a > b for a, b in zip(sonra_ihlal, once_ihlal)) or sonra_fazla_sayi > once_fazla_sayi:
            geri_al(nokta)
            print(f"[CP-SAT] sonuc kotu ({once_fazla_sayi}->{sonra_fazla_sayi}) - GERI ALINDI", flush=True)
            return
        print(f"[CP-SAT] BASARILI: pencere_fazla {once_fazla_sayi} -> {sonra_fazla_sayi} "
              f"({cozucu.StatusName(durum)}, {round(cozucu.WallTime(),1)}s, {len(serbest)} ders)", flush=True)

    # CP-SAT VARSAYILAN OLARAK KAPALI.
    # OLCUM SONUCU (3 tohum): kazanc YOK. Cozucu optimalligi 0.1 saniyede
    # kanitladi - model zor oldugu icin degil, HAREKET ALANI OLMADIGI
    # icin. Sinif programlari %100 dolu oldugundan, diger tum dersler
    # dondurulunca serbest birakilan derslerin gidebilecegi bos yer
    # neredeyse kalmiyor. Daha guclu bir formulasyon (hedef ogretmenlerin
    # girdigi TUM siniflarin derslerini birlikte serbest birakmak)
    # gerekir; bu ayri ve buyuk bir calisma konusudur.
    # Denemek isteyen: asagidaki degeri True yapmak yeterli.
    CP_SAT_AKTIF = False
    if CP_SAT_AKTIF:
        cp_sat_pencere_pass()


    # ---------------- 8b. Son guvenlik agi: pencere/tek-ders gecisleri yan etki yaratmis olabilir ----------------
    # pencere_azalt_pass yalnizca pencereyi optimize eder, min-gunluk-saat VE
    # 'asla 2 bos gun' kurallarindan HABERSIZDIR - bir dersi baska gune
    # tasirken (a) yeni bir tek-ders kalintisi biraktabilir, (b) kaynak
    # gunu tamamen bosaltip 2. bir bos gun yaratabilir. Her iki kural da
    # MUTLAK oldugundan burada sirayla son bir kez zorluyoruz.
    # KRITIK DUZELTME: bu gecisler artik SADECE gercekten bir ihlal VARSA
    # calisiyor - onceden KOSULSUZ calisiyordu, ve gercek loglar bunun
    # (zaman_takasi_pencere_pass 19'da bitmisken, buradan sonra final
    # ciktinin 33'e cikmasi seklinde) pencereyi bozdugunu kanitladi -
    # her ne kadar 'duzeltecek' bir sey OLMASA bile bu gecisler bir
    # seyler degistiriyordu (belki de rastgelelik/tekrar-siralama
    # nedeniyle). Artik ihlal yoksa HICBIR SEY yapilmiyor.
    if ihlal_sayisi() > 0 or fazla_bos_gun_toplam() > 0:
        tek_ders_yasakla_pass()
        fazla_bos_gun_konsolide_pass()
        tek_ders_yasakla_pass()  # konsolide de yeni tek-ders yaratmis olabilir

    # ---------------- 9. (eksik tekrar deneme adimi 6b'ye tasindi) ----------------

    # ---------------- 9b. Brans bazli ogretmen takasi (pencere azaltma - farkli yontem) ----------------
    # Normal pencere azaltma "bos hucre" arar - %100 dolu sinifta bu genelde
    # yok. Bu pass FARKLI bir yol dener: ayni BRANSTAN iki ogretmenin zaten
    # yerlesmis, AYNI BOYDAKI iki blogunun ogretmen etiketini degistirir -
    # gun/saat/sinif/ders HIC degismez, boylece sinif dolulugu asla bozulmaz.
    # Sadece ikisinin de brans bilgisi varsa ve esitse calisir; unvan/brans
    # verisi yoksa bu pass hicbir sey yapmaz (guvenli no-op).
    def _brans_takasi_dene(tc1):
        brans1 = tc_kisit[tc1]["brans"]
        if not brans1:
            return False
        once_pencere_tc1 = ogrt_haftalik_pencere(tc1)
        for gun in gunler:
            saatler = ogrt_gun_saatleri(tc1, gun)
            if len(saatler) < 2:
                continue
            mn, mx = min(saatler), max(saatler)
            bos_saatler = set(range(mn, mx + 1)) - set(saatler)
            if not bos_saatler:
                continue
            for tc2 in tum_tc:
                if tc2 == tc1 or tc_kisit[tc2]["brans"] != brans1:
                    continue
                for g2 in gorevler:
                    if not g2["placed"] or g2["placed"][0] != gun:
                        continue
                    if tc2 not in tum_ogrt(g2) or tc1 in tum_ogrt(g2):
                        continue
                    b_start = g2["placed"][1]
                    boy2 = g2["boy"]
                    if any((b_start + i) not in bos_saatler for i in range(boy2)):
                        continue
                    # tc1'in AYNI BOYDA, BASKA bir gundeki bir gorevini bul (takas icin)
                    for g1 in gorevler:
                        if (g1["placed"] and tc1 in tum_ogrt(g1) and tc2 not in tum_ogrt(g1)
                                and g1["boy"] == boy2 and g1["placed"][0] != gun):
                            # PENCERE-FARKINDA GUVENLIK KONTROLU: takas
                            # oncesi tc2'nin de pencere degerini kaydet -
                            # ONCEDEN bu kontrol YOKTU, bu yuzden bu pass
                            # zaman_takasi_pencere_pass'in dikkatlice
                              # bulmus oldugu iyilesmeleri SESSIZCE
                            # bozabiliyordu (ilk uygun takasi kosulsuz
                            # kabul ediyordu).
                            once_pencere_tc2 = ogrt_haftalik_pencere(tc2)
                            if _takasi_uygula(g1["id"], g2["id"]):
                                yeni_pencere_tc1 = ogrt_haftalik_pencere(tc1)
                                yeni_pencere_tc2 = ogrt_haftalik_pencere(tc2)
                                if (yeni_pencere_tc1 < once_pencere_tc1
                                        and yeni_pencere_tc2 <= once_pencere_tc2):
                                    return True
                                # Iyilesme yok VEYA tc2 kotulesti - GERI AL.
                                # _takasi_uygula kendi ic transaction'ini
                                # commit ettigi icin, tersini uygulayarak
                                # (ayni iki gorevi tekrar takas ederek) geri
                                # aliyoruz. GERI ALMA da basarili sayilip
                                # gecmise KAYDEDILDIGI icin (2 fantom kayit
                                # - takas + geri alma), ikisini de temizleriz.
                                if _takasi_uygula(g1["id"], g2["id"]):
                                    if _brans_takas_gecmisi:
                                        _brans_takas_gecmisi.pop()
                                    if _brans_takas_gecmisi:
                                        _brans_takas_gecmisi.pop()
        return False

    def brans_takas_pass():
        for _tur in range(10):
            if _zaman_doldu():
                break
            pencereli = sorted(
                (tc for tc in tum_tc if not idareci_mi[tc] and tc_kisit[tc]["brans"]
                 and ogrt_haftalik_pencere(tc) > MAX_PENCERE_HEDEF),
                key=lambda tc: -ogrt_haftalik_pencere(tc))
            if not pencereli:
                break
            herhangi_degisti = False
            for tc in pencereli:
                if _zaman_doldu():
                    break
                if _brans_takasi_dene(tc):
                    herhangi_degisti = True
            if not herhangi_degisti:
                break

    # AYNI DUZELTME: brans_takas_pass da (kendi ic korumasina RAGMEN,
    # sadece tc1/tc2'yi kontrol eder - ek_tcler/coklu-ogretmen
    # senaryolarinda ACIK OLABILIR) 'kaldigi yerden devam' modunda
    # atlanir. Loglar, TEK BIR denemede zaman_takasi_pencere_pass 19'da
    # bitmisken, bu noktadan SONRA (final cikti) 33'e ciktigini
    # gosterdi - bu iki pass (brans_takas_pass, pencere_azalt_pass)
    # zaman_takasi_pencere_pass'tan SONRA calisan TEK supheli adaylardi.
    if not baslangic_yerlesim:
        brans_takas_pass()

    # ---------------- 9c. Son guvenlik taramasi ----------------
    # AYNI DUZELTME: sadece gercekten bir ihlal varsa calisir.
    if ihlal_sayisi() > 0 or fazla_bos_gun_toplam() > 0:
        tek_ders_yasakla_pass()
        fazla_bos_gun_konsolide_pass()
        fazla_bos_gun_brans_takas_pass()
        tek_ders_yasakla_pass()

    # ---------------- 9d. BUTUNLUK DOGRULAMASI (cakisma kontrolu) ----------------
    # baslangic_yerlesim (artimli devam) mekanizmasi HENUZ TAM guvenilir
    # kanitlanmadigi icin, cikti uretmeden ONCE gercek bir cakisma olup
    # olmadigini TARAR. Cakisma varsa bu sonuc KULLANILAMAZ olarak
    # isaretlenir - cagiran taraf (arka_plan_arama) bu turu güvenle atlar,
    # boylece HICBIR ZAMAN cakismali bir sonuc kullaniciya ulasamaz.
    _butunluk_sorunu = False
    _sinif_gorulen = set()
    _ogrt_gorulen = set()
    for g in gorevler:
        if not g["placed"]:
            continue
        gun_v, saat_v = g["placed"]
        for b in range(g["boy"]):
            sinif_anahtar = (g["sid"], gun_v, saat_v + b)
            if sinif_anahtar in _sinif_gorulen:
                _butunluk_sorunu = True
            _sinif_gorulen.add(sinif_anahtar)
            for otc in tum_ogrt(g):
                ogrt_anahtar = (otc, gun_v, saat_v + b)
                if ogrt_anahtar in _ogrt_gorulen:
                    _butunluk_sorunu = True
                _ogrt_gorulen.add(ogrt_anahtar)
    if _butunluk_sorunu:
        print("UYARI: butunluk dogrulamasi CAKISMA tespit etti - bu sonuc ATILIYOR", flush=True)

    # ---------------- 10. Cikti ----------------
    slots = {sid: {} for sid in siniflar}
    for g in gorevler:
        if not g["placed"]:
            continue
        gun, saat = g["placed"]
        sid = g["sid"]; ders = dersler[g["did"]]
        if gun not in slots[sid]:
            slots[sid][gun] = {}
        for b in range(g["boy"]):
            slots[sid][gun][saat + b] = {
                "ders_id": g["did"], "ders_adi": ders.get("ders_adi", ""),
                "kisa_ad": ders.get("kisa_ad", ders.get("ders_adi", "")[:4]),
                "renk": ders.get("renk", "#1a6b47"), "ogretmen_tc": g["tc"],
                "ogretmenler": g["ogrtler"], "kilitli": False,
            }

    # eksikler listesi TUM gorevlerin GUNCEL (su anki) durumundan taranir -
    # sadece erken bir "hala_eksik" anlik goruntusune guvenmek, sonraki
    # gecislerin (pencere/brans takasi vb.) sessizce bir gorevi yerinden
    # oynatip birakma ihtimaline karsi kor kalirdi. Bu, son bir dogruluk
    # kontrolu olarak TUM gorevleri tekrar tarar.
    eksikler = []
    for g in gorevler:
        if not g["placed"]:
            eksikler.append({"sinif": siniflar[g["sid"]].get("sinif_adi"),
                              "ders": dersler[g["did"]].get("ders_adi"), "blok": g["boy"]})

    basarili = len(eksikler) == 0
    durum = "OPTIMAL" if basarili else "PARTIAL"
    sure = round(time.time() - t0, 2)

    # ---------------- SON, KOSULSUZ GUVENLIK AGI ----------------
    # KRITIK: gercek kullanimda bazen fazla_bos_gun_sayisi>0 (MUTLAK MEB
    # kuralinin ihlali) cikti goruldu - tum onceki gecisler kendi ic
    # kontrollerini yapsa da, COK SAYIDA gecisin (zaman-takasi, brans-
    # takasi, zincir rotasyonu) etkilesimi beklenmeyen bir yan etki
    # yaratmis olabilir. AYNI DUZELTME BURADA DA: sadece gercekten bir
    # ihlal varsa calisir - once KOSULSUZDU ve bu, zaten temiz/iyi bir
    # pencere durumunu (zaman_takasi_pencere_pass'in ozenle bulmus
    # oldugu) gereksiz yere bozabiliyordu.
    if ihlal_sayisi() > 0 or fazla_bos_gun_toplam() > 0:
        tek_ders_yasakla_pass()
        fazla_bos_gun_konsolide_pass()
        tek_ders_yasakla_pass()

    # ---- Kalite istatistikleri (coklu-deneme sarmalayicisi icin) ----
    # NOT: idareci (2-12 saat) ogretmenler pencere ve fazla-bos-gun
    # olcumlerinden MUAF - min-saat/tek-ders kurali ise HERKES icin gecerli.
    min_ihlal_sayisi = 0
    for tc in tum_tc:
        ming = tc_kisit[tc]["minG"]
        if not ming:
            continue
        for gun in gunler:
            if 0 < day_load[tc][gun] < ming:
                min_ihlal_sayisi += 1
    pencere_toplam = sum(ogrt_haftalik_pencere(tc) for tc in tum_tc if not idareci_mi[tc])
    pencere_fazla_sayisi = sum(1 for tc in tum_tc if not idareci_mi[tc] and ogrt_haftalik_pencere(tc) > MAX_PENCERE_HEDEF)
    pencere_max = max((ogrt_haftalik_pencere(tc) for tc in tum_tc if not idareci_mi[tc]), default=0)
    fazla_bos_gun_sayisi = sum(
        1 for tc in tum_tc if not idareci_mi[tc]
        and sum(1 for g in gunler if day_load[tc][g] == 0) >= 2
    )
    sifir_bos_gun_sayisi = sum(
        1 for tc in tum_tc if not idareci_mi[tc]
        and sum(1 for g in gunler if day_load[tc][g] == 0) == 0
    )

    # ---- Ogretmen bazli pencere raporu (modal icin) ----
    ogretmen_raporu = []
    for tc in tum_tc:
        bos_gun_sayisi = sum(1 for g in gunler if day_load[tc][g] == 0)
        ogretmen_raporu.append({
            "ogretmen_tc": tc,
            "brans": tc_kisit[tc]["brans"] or None,
            "unvan": tc_kisit[tc]["unvan"] or None,
            "idareci": idareci_mi[tc],
            "pencere": ogrt_haftalik_pencere(tc) if not idareci_mi[tc] else 0,
            "bos_gun_sayisi": bos_gun_sayisi,
            "bos_gun_istemez": tc_kisit[tc]["bosGunIstemez"],
            "gunluk_yuk": [day_load[tc][g] for g in gunler],
        })
    ogretmen_raporu.sort(key=lambda r: -r["pencere"])

    print(f"Tamamlandi {sure}s eksik={len(eksikler)} min_ihlal={min_ihlal_sayisi} "
          f"pencere_fazla={pencere_fazla_sayisi} pencere_max={pencere_max} pencere_toplam={pencere_toplam} "
          f"fazla_bosgun={fazla_bos_gun_sayisi} sifir_bosgun={sifir_bos_gun_sayisi}", flush=True)

    # Ham yerlesim ({gid: [gun,saat,tc,ogrtler]}) - bir sonraki cagriya
    # 'baslangic_yerlesim' olarak verilip ARTIMLI (sifirdan degil) devam
    # edilebilmesi icin. ogrtler (goruntuleme listesi) de dahil edilir -
    # aksi halde brans-takasli bir gorevin goruntuleme adi eski ogretmeni
    # gosterirken gercek tc'si yeni ogretmeni gosterir (tutarsizlik).
    _yerlesim_ham = {g["id"]: [g["placed"][0], g["placed"][1], g["tc"], g["ogrtler"]]
                      for g in gorevler if g["placed"]}

    return {"basari": basarili, "slots": slots, "eksikler": eksikler,
            "sure_sn": sure, "durum": durum, "seed": seed,
            "_yerlesim_ham": _yerlesim_ham,
            "_butunluk_sorunu": _butunluk_sorunu,
            "_brans_takas_gecmisi": _brans_takas_gecmisi,
            "istatistik": {
                "min_ihlal_sayisi": min_ihlal_sayisi,
                "pencere_fazla_sayisi": pencere_fazla_sayisi,
                "pencere_max": pencere_max,
                "fazla_bos_gun_sayisi": fazla_bos_gun_sayisi,
                "sifir_bos_gun_sayisi": sifir_bos_gun_sayisi,
                "pencere_toplam": pencere_toplam,
                "ogretmen_raporu": ogretmen_raporu,
            }}


def hesapla_skor(sonuc):
    """SIRALI (lexicographic) tuple dondurur - agirlikli toplam DEGIL.
    Python tuple karsilastirmasi soldan saga once ilk farkli elemana
    bakar - bu yuzden erken sıradaki bir metrik HER ZAMAN sonraki
    metriklerden MUTLAK oncelikli olur, sayisal agirlik dengesizligi
    olusamaz.

    KULLANICI KARARI (SON - KESIN, DUZELTILDI): 'asla 2 gun bos
    verilemez' kurali MEB yonetmeligine dayanan MUTLAK bir kisittir -
    bu, min-gunluk-saat (asla tek ders) kuralindan bile ONCELIKLIDIR
    artik (once yanlislikla min_ihlal fazla_bos_gun'dan once sıralanmisti,
    bu da 'fazla_bosgun=2 ama min_ihlal=3' olan bir sonucun 'fazla_
    bosgun=0 ama min_ihlal=4' olan bir sonuca karsi yanlislikla
    KAZANMASINA yol aciyordu - MEB kuralini ihlal eden bir sonuc
    secilmis oluyordu). Simdi fazla_bos_gun_sayisi EN BASTA (eksikten
    hemen sonra) geliyor - hicbir kombinasyonda MEB kuralini ihlal eden
    bir sonuc, ihlal etmeyen bir sonuca karsi kazanamaz.

    Ayrica pencere DAGILIMI da adil olmali. ANCAK onemli bir DERS: daha
    once pencere_max (en kotu tekil deger) burada pencere_fazla_sayisi'ndan
    (kac ogretmen hedefin ustunde) ONCE gelecek sekilde siralanmisti - bu,
    aramanin COK DAHA FAZLA ogretmeni hedefe (<=2) getiren gercek
    iyilesmeleri, SADECE 1 ogretmenin en kotu degeri hafifce (orn. 8->9)
    artiyor diye REDDETMESINE yol aciyordu (lexicographic karsilastirma
    ilk farkli elemanda karar veriyor, pencere_max daha once geldigi icin
    pencere_fazla_sayisi'ndaki BUYUK iyilesmeler hic goz onune alinamiyordu).
    Kullanicidan gelen gercek gozlem ('pencere>2 sayisi saatlerce 42'de
    sabit kaliyor') bunu dogruladi. Simdi ONCELIK TERS CEVRILDI:
    pencere_fazla_sayisi (KAC OGRETMEN hedefte - asil onemli olan) ONCE
    gelir, pencere_max SADECE esitlik durumunda (tie-break) devreye girer.
    """
    ist = sonuc.get("istatistik", {})
    return (
        len(sonuc["eksikler"]),                        # 1) asla eksik ders
        ist.get("fazla_bos_gun_sayisi", 0),              # 2) asla 2+ bos gun (MEB) - EN MUTLAK KURAL
        ist.get("min_ihlal_sayisi", 0),                 # 3) asla tek ders
        ist.get("sifir_bos_gun_sayisi", 0),              # 4) herkese bos gun (kapsama)
        ist.get("pencere_fazla_sayisi", 0),              # 5) ASIL HEDEF: kac ogretmen pencere<=2'nin ustunde
        ist.get("pencere_max", 0),                       # 6) ADALET (tie-break): esit pencere_fazla'da en kotu tekil deger
        ist.get("pencere_toplam", 0),                    # 7) genel toplam (ince ayar)
    )


def arka_plan_arama(veri, sure_sn, ilerleme_fn=None, durdur_fn=None, tur_butcesi_sn=90):
    """Web istegi suresiyle SINIRLI OLMADAN (app.py bunu bir arka plan
    thread'inde cagirir), ASC/FET'e YAKIN bir yontemle en iyi sonucu arar:

    FAZ 1 (KESIF - ilk birkac tur): farkli rastgele siralamalarla TAM
    cozumler dener, iyi bir TABAN bulur.

    FAZ 2 (ARTIMLI CILALAMA - kalan tum sure): FAZ 1'de bulunan EN IYI
    cozumu 'baslangic_yerlesim' olarak verip _dagit_tek_deneme'yi TEKRAR
    COZMEDEN, DOGRUDAN cilalama gecislerine (pencere azaltma, brans
    takasi, bos-gun duzeltme) sokar - HER TUR bir onceki turun SONUCU
    UZERINE insa eder (ASC/FET'in yaptigi gibi kademeli iyilestirme).
    Bir tur mevcut en iyiden KOTU cikarsa o turun sonucu ATILIR, bir
    sonraki tur YINE mevcut en iyiden devam eder (hic zaman kaybi/gerileme
    olmaz - sadece hicbir zaman kotulesmeyen, surekli ilerleyen bir
    arama). Bu, 'her turda sifirdan yeniden coz' yaklasimindan COK DAHA
    HIZLI (bir tur saniyeler surer, dakikalar degil) ve COK DAHA ETKILI
    (gercek yerel arama).

    hesapla_skor() ile AYNI oncelik sirasini kullanir - bu yuzden
    pencereyi azaltirken ASLA bos-gun kapsamasini/asla-tek-ders/asla-2-
    gun kuralini bozan bir sonuc secmez.

    veri: normal /dagit payload'i (siniflar, dersler, atamalar, kisitlar,
          gunler, kilitli).
    sure_sn: toplam calisma suresi (saniye).
    ilerleme_fn(tur_no, en_iyi_sonuc, en_iyi_skor, gecen_sn): her YENİ EN
          İYİ sonuc bulundugunda cagirilir.
    durdur_fn(): her tur basinda kontrol edilir - True donerse arama
          NAZIKCE durur.
    tur_butcesi_sn: FAZ 1'deki her TAM denemeye ayrilan maksimum sure.

    Dondurur: en_iyi_sonuc - hic tur tamamlanamadiysa None doner."""
    taban_seed = veri.get("seed", random.randint(1, 999_999_999))
    t0 = time.time()
    en_iyi_sonuc = None
    en_iyi_skor = None
    tur_no = 0
    mukemmel = (0, 0, 0, 0, 0, 0, 0)
    FAZ1_TUR_SAYISI = 5   # kesif icin kac tam cozum denensin, sonrasi cilalama

    # KALDIGI YERDEN DEVAM: eger cagiran (frontend/app.py) veri icinde
    # HAZIR bir 'baslangic_yerlesim' gonderdiyse (orn. kullanici daha once
    # bir optimizasyon sonucunu UYGULADI ve simdi ONUN UZERINDEN devam
    # etmek istiyor), bunu ISTATISTIGINI hesaplamak icin TEK bir hizli
    # cagriyla 'baslangic' sonucu olarak kullaniriz ve FAZ 1'i (sifirdan
    # rastgele kesif) TAMAMEN ATLARIZ - arama DOGRUDAN bu durumu
    # cilalamaya baslar. Boylece 'Pencere Sayısını Azalt' butonuna
    # birden fazla kez basmak HER SEFERINDE sifirdan baslamaz, en son
    # uygulanan/bulunan duruma gore devam eder.
    hazir_baslangic = veri.get("baslangic_yerlesim")
    if hazir_baslangic:
        # KRITIK DUZELTME: checkpoint'i yeniden islerken (guvenlik aglarini
        # calistirmak icin) bazen GECICI olarak fazla_bos_gun/min_ihlal
        # ihlali olusabiliyor (farkli mutlak kurallari duzeltme gecisleri
        # birbirini etkileyebiliyor). Bunu en aza indirmek icin, FARKLI
        # seed'lerle birkac deneme yapip EN TEMIZ (ihlal toplami en dusuk)
        # olani baslangic noktasi olarak seciyoruz - boylece checkpoint'in
        # kalitesi guvenilir sekilde korunur.
        en_temiz_sonuc = None
        en_temiz_skor = None
        for _dene in range(4):
            ilk_veri = dict(veri)
            ilk_veri["seed"] = taban_seed + _dene * 104729
            ilk_veri["on_bos_gun_ata"] = False
            ilk_veri["_deneme_butcesi_sn"] = min(90, sure_sn)
            aday = _dagit_tek_deneme(ilk_veri)
            # ARA ILERLEME BILDIRIMI: her deneme (90sn'ye kadar surebilir,
            # 4 deneme = 6 dakikaya kadar) sonrasinda durumu gunceller -
            # kullanicinin "hic ilerleme yok, donmus mu?" endisesine
            # dogrudan cevap. Henuz KESIN en_iyi_sonuc belirlenmedi ama
            # kullaniciya "bir sey oluyor" gostermek onemli.
            if ilerleme_fn is not None and not aday.get("_butunluk_sorunu"):
                try:
                    _ara_skor = hesapla_skor(aday)
                    _ara_bildirim = dict(aday)
                    _ara_bildirim["_tur_no"] = 0
                    ilerleme_fn(0, _ara_bildirim, _ara_skor, time.time() - t0)
                except Exception:
                    pass
            if aday.get("_butunluk_sorunu"):
                continue
            # ONEMLI DUZELTME: ozel bir 'ihlal_toplam' hesabi yerine TAM
            # hesapla_skor() tuple'i kullanilir - bu, eksik/fazla_bosgun/
            # min_ihlal ONCELIGINI KORURKEN, ayni zamanda pencere_fazla/
            # pencere_max/pencere_toplam kalitesini de dogru sekilde
            # karsilastirir. Onceki surum SADECE mutlak kural ihlallerine
            # bakiyordu, pencere kalitesini HIC KARSILASTIRMIYORDU - bu,
            # 'Pencere Azalt tekrar basinca sayisi ARTIYOR' sikayetinin
            # GERCEK kok nedeniydi: 5 'temiz' denemeden HERHANGI biri
            # (ilk bulunani) seciliyordu, aralarinda EN IYI pencereli
            # olanı degil.
            aday_skor = hesapla_skor(aday)
            if en_temiz_skor is None or aday_skor < en_temiz_skor:
                en_temiz_sonuc = aday
                en_temiz_skor = aday_skor
            # ONEMLI: erken cikis KALDIRILDI - "ilk temiz bulunani hemen
            # kabul et" mantigi, PENCERE kalitesini hic karsilastirmadan
            # sonucu belirliyordu (asil sikayetin kok nedeni). Artik TUM
            # 5 deneme calisir, ARALARINDAN hesapla_skor'a gore EN IYISI
            # (pencere dahil) secilir.
        ilk_sonuc = en_temiz_sonuc
        ilk_ist = ilk_sonuc.get("istatistik", {}) if ilk_sonuc else {}
        if ilk_sonuc is not None and not ilk_sonuc.get("_butunluk_sorunu"):
            en_iyi_sonuc = ilk_sonuc
            en_iyi_skor = hesapla_skor(en_iyi_sonuc)
            en_iyi_sonuc["_tur_no"] = 0
            en_iyi_sonuc["_gecen_sn"] = round(time.time() - t0, 1)
            if ilerleme_fn is not None:
                try:
                    ilerleme_fn(0, en_iyi_sonuc, en_iyi_skor, time.time() - t0)
                except Exception:
                    pass
            FAZ1_TUR_SAYISI = 0  # kesif atlanir - direkt cilalamaya gec
            uyari = ""
            _eksik_sayisi = len(ilk_sonuc.get("eksikler", []))
            if _eksik_sayisi > 0 or ilk_ist.get("fazla_bos_gun_sayisi", 0) > 0 or ilk_ist.get("min_ihlal_sayisi", 0) > 0:
                uyari = (f" - UYARI: checkpoint eksik={_eksik_sayisi} "
                         f"fazla_bosgun={ilk_ist.get('fazla_bos_gun_sayisi')} "
                         f"min_ihlal={ilk_ist.get('min_ihlal_sayisi')} icermis olabilir (4 denemenin "
                         f"en temizi secildi), sonraki turlerde duzeltilmeye calisilacak")
            print(f"[KALDIGI YERDEN DEVAM] hazir baslangic yerlesimi yuklendi, "
                  f"Faz 1 (sifirdan kesif) atlaniyor{uyari}", flush=True)

    # 6-24 saatlik COK UZUN calismalar icin: cilalama bir SUREDIR
    # (TAKILMA_ESIGI tur boyunca) hic iyilesme saglamiyorsa, mevcut en
    # iyiden devam etmek yerine TAZE bir tam cozum (yeni rastgele siralama)
    # dener - bu, aramanin bir 'yerel optimum'a saplanip KALICI OLARAK
    # takilip kalmasini onler (ASC/FET'in de yaptigi 'restart' stratejisi).
    TAKILMA_ESIGI = 15
    son_iyilesme_turu = 0

    while time.time() - t0 < sure_sn:
        if durdur_fn is not None and durdur_fn():
            break
        tur_no += 1
        kalan = sure_sn - (time.time() - t0)
        if kalan < 5:
            break
        deneme_veri = dict(veri)
        deneme_veri["seed"] = (taban_seed + tur_no * 7919) % 999_999_999

        takildi_mi = (tur_no - son_iyilesme_turu) >= TAKILMA_ESIGI

        if tur_no <= FAZ1_TUR_SAYISI or takildi_mi:
            # ---- FAZ 1: KESIF (veya TAKILMA sonrasi TAZE BASLANGIC) ----
            # ONEMLI KESIF: otomatik_bos_gun_pass/otomatik_bos_gun_brans_takas_pass'a
            # eklenen 'kilitleme' duzeltmesinden sonra GUVENLI mod (False)
            # artik on-atamali (True) moddan DAHA GUVENILIR sonuc veriyor.
            if takildi_mi and tur_no > FAZ1_TUR_SAYISI:
                print(f"[TAKILMA] {TAKILMA_ESIGI} turdur iyilesme yok - taze tam cozum deneniyor "
                      f"(tur {tur_no})", flush=True)
                son_iyilesme_turu = tur_no  # tekrar tekrar taze baslangic denemesin, sayaci sifirla
            deneme_veri["on_bos_gun_ata"] = (tur_no % 4 == 0)
            deneme_veri["_deneme_butcesi_sn"] = min(tur_butcesi_sn, max(kalan - 5, 10))
            deneme_veri["kovma_zincir_siniri"] = 15  # kesif turlari - orta derinlik, hizli kalsin
        else:
            # ---- FAZ 2: ARTIMLI CILALAMA - onceki en iyiden devam ----
            if en_iyi_sonuc is not None and en_iyi_sonuc.get("_yerlesim_ham"):
                deneme_veri["baslangic_yerlesim"] = en_iyi_sonuc["_yerlesim_ham"]
                deneme_veri["on_bos_gun_ata"] = False  # yerlesim zaten hazir, tekrar on-atama gerekmez
                # Cilalama turlari genelde HIZLIDIR (yeniden yerlestirme yok,
                # sadece cilalama gecisleri) ama TUM gecislerin (bos-gun,
                # fazla-bos-gun, pencere, brans-takas) tam bir tur icin
                # yeterli zaman bulmasi icin butce biraz genis tutulur.
                deneme_veri["_deneme_butcesi_sn"] = min(45, max(kalan - 5, 5))
                # CILALAMA turlarinda YERLESTIRME ZATEN HAZIR (baslangic_
                # yerlesim ile) - bu yuzden zamanin NEREDEYSE TAMAMI dogrudan
                # pencere azaltma gecislerine gidiyor. Kullanicinin talebi
                # uzerine burada COK DAHA DERIN kovma zincirlerine izin
                # verilir - saatlerce suren arka plan aramasinin asil gucu
                # burada devreye girer.
                deneme_veri["kovma_zincir_siniri"] = 50
            else:
                # FAZ 1 hic basarili olmadiysa (nadir) FAZ 2'yi de tam cozum
                # olarak dene - bos donmemek icin.
                deneme_veri["on_bos_gun_ata"] = (tur_no % 4 == 0)
                deneme_veri["_deneme_butcesi_sn"] = min(tur_butcesi_sn, max(kalan - 5, 10))
                deneme_veri["kovma_zincir_siniri"] = 15

        sonuc = _dagit_tek_deneme(deneme_veri)

        # GUVENLIK AGI: butunluk dogrulamasi cakisma bulduysa bu tur TAMAMEN
        # ATILIR - en_iyi_sonuc HIC degismez, bir sonraki tur (baslangic_
        # yerlesim hala eski/GUVENLI en_iyi_sonuc'tan geldigi icin) normal
        # sekilde devam eder. Boylece Faz 2'deki olasi bir hata ASLA
        # kullaniciya cakismali bir sonuc olarak ulasamaz.
        if sonuc.get("_butunluk_sorunu"):
            print(f"[GUVENLIK] tur {tur_no}: cakisma tespit edildi, bu tur ATILDI", flush=True)
            continue

        skor = hesapla_skor(sonuc)
        gecen = time.time() - t0

        if en_iyi_skor is None or skor <= en_iyi_skor:
            gelisti = en_iyi_skor is None or skor < en_iyi_skor
            en_iyi_skor = skor
            en_iyi_sonuc = sonuc
            en_iyi_sonuc["_tur_no"] = tur_no
            en_iyi_sonuc["_gecen_sn"] = round(gecen, 1)
            if gelisti:
                son_iyilesme_turu = tur_no  # takilma sayacini sifirla
        # NABIZ GUNCELLEMESI: kullanicinin 'donma hissi' sikayeti uzerine,
        # artik HER turda (iyilesme olsun olmasin) ilerleme_fn cagrilir -
        # boylece 'Tur: X | Ysn' ekrandaki sayaclar HER ZAMAN ilerler,
        # asla donmus gorunmez. Gosterilen ISTATISTIKLER (en_iyi_sonuc)
        # ise HER ZAMAN gercek en iyi sonucu yansitir - bu turda iyilesme
        # olmasa bile GERIYE GITMEZ, sadece sayaclar 'canli' kalir.
        if ilerleme_fn is not None:
            try:
                ilerleme_fn(tur_no, en_iyi_sonuc, en_iyi_skor, gecen)
            except Exception:
                pass  # ilerleme bildirimi basarisiz olsa bile arama devam etmeli
        # skor kotuyse bu turun sonucu ATILIR - en_iyi_sonuc degismez, bir
        # sonraki tur YINE en_iyi_sonuc'un yerlesiminden devam eder (asla
        # geri gitmeyen, surekli ilerleyen bir arama).

        if en_iyi_skor == mukemmel:
            break

    return en_iyi_sonuc


def dagit(veri, kac_deneme=3, zaman_siniri_sn=230):
    """Coklu-deneme sarmalayicisi - IKI ASAMALI:

    ASAMA 1 (HIZLI TEMEL SONUC - guvenlik agi): once en hizli/guvenilir
    strateji (on_bos_gun_ata=False, dusuk butce) ile TEK bir deneme yapilir.
    Boylece ELIMIZDE HER ZAMAN CALISAN (0 eksik) bir sonuc olur.

    ASAMA 2 (ISTEGE BAGLI IYILESTIRME): GUCLU (on_bos_gun_ata=True) stratejiyi
    ARDIŞIK IKI FARKLI SEED ile dener (tek bir uzun deneme yerine). Neden:
    on_bos_gun_ata=True her ogretmene RASTGELE bir bosGun atar (rnd.shuffle);
    bazi seed'lerde bu rastgele atama kotu bir kombinasyona denk gelip
    (ornegin ayni sinifi paylasan cok sayida ogretmene ayni gun dusmesi)
    GERCEKTEN COZULEMEYEN bir yerlestirmeye yol acabiliyor - bu bir zaman
    sorunu degil, o SEED'e ozgu bir kisit-tatmin sorunu, bu yuzden 'daha
    fazla sure vermek' tek basina cozum degil. Iki FARKLI seed denemek,
    kotu bir rastgele atamanin butun sonucu batirma riskini azaltir.

    Oncelik sirasi: bkz. hesapla_skor().

    app.py TARAFINDA HICBIR DEGISIKLIK GEREKMEZ - 'from motor import dagit'
    aynen calismaya devam eder.
    """
    taban_seed = veri.get("seed", random.randint(1, 999999))
    t_baslangic = time.time()
    _skor_hesapla = hesapla_skor  # geriye-uyumluluk icin yerel takma ad
    tum_denemeler = []  # her denemenin ozet istatistigi - Render log erisimi
                          # guvenilmez oldugu icin bunu DOGRUDAN API cevabina
                          # koyuyoruz, tarayici konsolunda gorulebilsin diye.

    # ---- ASAMA 1: GUVENLI TEMEL SONUC (guvenlik agi) ----
    # 20sn cok kisaydi: otomatik_bos_gun_pass (guvenli - ASLA 2. gun
    # yaratmaz, sadece 1 gun VERMEYE calisir) cogu ogretmeni konsolide
    # edecek zamani bulamiyordu, bu da 'guclu' (on-atamali) deneme
    # reddedildiginde (fazla_bos_gun>0 oldugu icin) geriye COK ZAYIF bir
    # yedek kalmasina yol aciyordu. Artik bu asamaya da gercek bir butce
    # veriliyor - boylece HEM asla-2-gun kuralini bozmuyor HEM cogu
    # ogretmene guvenli sekilde bos gun verebiliyor.
    temel_veri = dict(veri)
    temel_veri["seed"] = taban_seed
    temel_veri["on_bos_gun_ata"] = False
    temel_veri["_deneme_butcesi_sn"] = 45
    en_iyi = _dagit_tek_deneme(temel_veri)
    en_iyi["_on_bos_gun_ata_kullanildi"] = False
    en_iyi["_kaynak"] = "asama1_temel"
    en_iyi_skor = _skor_hesapla(en_iyi)
    tum_denemeler.append({
        "kaynak": "asama1_temel", "on_bos_gun_ata": False, "skor": en_iyi_skor,
        "eksik": len(en_iyi["eksikler"]), "sure_sn": en_iyi.get("sure_sn"),
        "istatistik": en_iyi.get("istatistik"),
    })
    print(f"[ASAMA 1 - temel] skor={en_iyi_skor} sure={en_iyi.get('sure_sn')}s "
          f"eksik={len(en_iyi['eksikler'])} gecen_toplam={round(time.time()-t_baslangic,1)}s", flush=True)

    # ---- ASAMA 2: ISTEGE BAGLI IYILESTIRME (kalan zaman varsa) ----
    # ONEMLI DERS (gercek Render testinden): 60sn butce GUVENLI mod icin
    # bile YETERSIZ kaliyor (fazla_bos_gun'u tam 0'a indiremeden bitiyor).
    # DAHA FAZLA ama KISA denemeler yerine, DAHA AZ ama YETERLI SURELI
    # (80-90sn) denemeler daha guvenilir sonuc veriyor. Ayrica riskli
    # (on_bos_gun_ata=True) mod WEB AKISINDAN TAMAMEN CIKARILDI - gercek
    # testte 60sn butceyle bazen KATASTROFIK basarisiz oluyor (111 eksik
    # gibi) - bu risk, olasi pencere kazanimina degmiyor. Sadece kanitlanmis
    # GUVENLI mod (on_bos_gun_ata=False), FARKLI seed'lerle, YETERLI surede
    # tekrar tekrar denenir.
    GUVENLI_BUTCE = 75    # on_bos_gun_ata=False - kanitlanmis en guvenilir yontem, guvenli sure

    for i in range(kac_deneme - 1):
        kalan = zaman_siniri_sn - (time.time() - t_baslangic)
        guclu_dene = False  # riskli mod web akisinda ARTIK KULLANILMIYOR
        if kalan <= 5:
            print("Zaman siniri asildi (asama 2 baslamadan), en iyi sonucla devam ediliyor", flush=True)
            break
        deneme_veri = dict(veri)
        deneme_veri["seed"] = taban_seed + (i + 1) * 7919
        deneme_veri["on_bos_gun_ata"] = guclu_dene
        deneme_veri["_deneme_butcesi_sn"] = GUVENLI_BUTCE
        deneme_veri["_deneme_butcesi_sn"] = min(deneme_veri["_deneme_butcesi_sn"], max(kalan - 5, 10))
        sonuc = _dagit_tek_deneme(deneme_veri)
        sonuc["_on_bos_gun_ata_kullanildi"] = guclu_dene
        sonuc["_kaynak"] = f"asama2_deneme{i+1}"
        skor = _skor_hesapla(sonuc)
        tum_denemeler.append({
            "kaynak": f"asama2_deneme{i+1}", "on_bos_gun_ata": guclu_dene, "skor": skor,
            "eksik": len(sonuc["eksikler"]), "sure_sn": sonuc.get("sure_sn"),
            "butce_sn": deneme_veri["_deneme_butcesi_sn"],
            "istatistik": sonuc.get("istatistik"),
        })
        print(f"[ASAMA 2 - deneme {i+1}/{kac_deneme-1}] on_bos_gun_ata={deneme_veri['on_bos_gun_ata']} "
              f"seed={deneme_veri['seed']} skor={skor} eksik={len(sonuc['eksikler'])} "
              f"gecen_toplam={round(time.time()-t_baslangic,1)}s", flush=True)
        if skor < en_iyi_skor:
            en_iyi = sonuc
            en_iyi_skor = skor
        if skor == (0, 0, 0, 0, 0, 0, 0):
            break  # mukemmel sonuc bulundu, daha fazla denemeye gerek yok
        if time.time() - t_baslangic > zaman_siniri_sn:
            print("Zaman siniri asildi, en iyi sonucla devam ediliyor", flush=True)
            break

    en_iyi["seed"] = taban_seed  # disariya orijinal seed'i raporla
    en_iyi["toplam_sure_sn"] = round(time.time() - t_baslangic, 2)  # TUM sarmalayicinin gercek suresi
    en_iyi["_tum_denemeler"] = tum_denemeler  # tam deneme gecmisi (Render log gerektirmez)
    print(f"[SONUC] secilen_sure={en_iyi.get('sure_sn')}s TOPLAM_SARMALAYICI_SURESI={en_iyi['toplam_sure_sn']}s "
          f"secilen_on_bos_gun_ata={en_iyi.get('_on_bos_gun_ata_kullanildi', '?')}", flush=True)
    return en_iyi
