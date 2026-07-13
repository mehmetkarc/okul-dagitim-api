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
    "seed": int (opsiyonel)
  }

Cikti CP-SAT versiyonuyla AYNI:
  {"basari":bool, "slots":{sid:{gun:{saat:{...}}}}, "eksikler":[...],
   "sure_sn":float, "durum":str, "seed":int}
"""
import time
import random


def dagit(veri):
    t0 = time.time()
    siniflar  = {str(s["id"]): s for s in veri.get("siniflar", [])}
    dersler   = {str(d["id"]): d for d in veri.get("dersler", [])}
    atamalar  = {str(k): v for k, v in veri.get("atamalar", {}).items()}
    kisitlar  = {str(k): v for k, v in veri.get("kisitlar", {}).items()}
    gun_bilgi = {int(g["gun"]): int(g["saat"]) for g in veri.get("gunler", [])}
    gunler    = sorted(gun_bilgi.keys())
    seed      = veri.get("seed", random.randint(1, 999999))
    rnd = random.Random(seed)

    # ---------------- 1. Gorev listesi ----------------
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
            bloklar = ders.get("blok_dagilim") or [ders.get("haftalik_saat", 1)]
            for bi, boy in enumerate(bloklar):
                if not boy:
                    continue
                gorevler.append({
                    "id": f"{sid}_{did}_{bi}", "sid": sid, "did": did, "tc": tc,
                    "ogrtler": atama.get("ogretmenler", []), "boy": int(boy),
                    "placed": None,
                })

    if not gorevler:
        return {"basari": True, "slots": {sid: {} for sid in siniflar},
                "eksikler": [], "sure_sn": 0, "durum": "EMPTY", "seed": seed}

    tum_tc = sorted(set(g["tc"] for g in gorevler if g["tc"]))
    print(f"Gorev:{len(gorevler)} Sinif:{len(siniflar)} Ogretmen:{len(tum_tc)}", flush=True)

    # ---------------- 2. Kisit tablosu ----------------
    def kisit_al(tc):
        k = kisitlar.get(tc, {})
        return {
            "bosGun": int(k["bosGun"]) if k.get("bosGun") else None,
            "kapali": set(int(v) for v in k.get("kapaliGunler", [])),
            "minG":   int(k["minGunlukSaat"]) if k.get("minGunlukSaat") else None,
            "maxG":   int(k["maxGunlukSaat"]) if k.get("maxGunlukSaat") else None,
        }
    tc_kisit = {tc: kisit_al(tc) for tc in tum_tc}

    # ---------------- 3. Doluluk gridleri ----------------
    class_occ   = {sid: {} for sid in siniflar}        # {(gun,saat): gid}
    teacher_occ = {tc: {} for tc in tum_tc}             # {(gun,saat): gid}
    gun_ders    = {}                                     # (sid,did) -> {gun: adet}
    day_load    = {tc: {g: 0 for g in gunler} for tc in tum_tc}
    gid_map     = {g["id"]: g for g in gorevler}

    def bosalt(gid):
        g = gid_map[gid]
        if not g["placed"]:
            return
        gun, saat = g["placed"]
        for b in range(g["boy"]):
            class_occ[g["sid"]].pop((gun, saat + b), None)
            if g["tc"]:
                teacher_occ[g["tc"]].pop((gun, saat + b), None)
        key = (g["sid"], g["did"])
        if key in gun_ders and gun in gun_ders[key]:
            gun_ders[key][gun] -= 1
            if gun_ders[key][gun] <= 0:
                del gun_ders[key][gun]
        if g["tc"]:
            day_load[g["tc"]][gun] -= g["boy"]
        g["placed"] = None

    def yerlestir(gid, gun, saat):
        g = gid_map[gid]
        for b in range(g["boy"]):
            class_occ[g["sid"]][(gun, saat + b)] = gid
            if g["tc"]:
                teacher_occ[g["tc"]][(gun, saat + b)] = gid
        key = (g["sid"], g["did"])
        gun_ders.setdefault(key, {})
        gun_ders[key][gun] = gun_ders[key].get(gun, 0) + 1
        if g["tc"]:
            day_load[g["tc"]][gun] += g["boy"]
        g["placed"] = (gun, saat)

    def musait_mi(gid, gun, saat):
        g = gid_map[gid]
        boy = g["boy"]; sid = g["sid"]; did = g["did"]; tc = g["tc"]
        if saat < 1 or saat + boy - 1 > gun_bilgi[gun]:
            return False
        if tc:
            k = tc_kisit[tc]
            if k["bosGun"] == gun or gun in k["kapali"]:
                return False
        for b in range(boy):
            s = saat + b
            if (gun, s) in class_occ[sid]:
                return False
            if tc and (gun, s) in teacher_occ[tc]:
                return False
        key = (sid, did)
        if gun_ders.get(key, {}).get(gun, 0) >= 1:
            return False  # ayni ders ayni gun tekrar olamaz
        if tc:
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
            if mevcut > 0:
                s -= 5  # zaten kullanilan gunu tercih et (bos gun biriktirmek icin)
            ming = k["minG"]
            if ming and 0 < mevcut < ming:
                s -= 8  # min saat altindaki gunu tamamlamaya oncelik ver
            if (gun, saat - 1) in teacher_occ.get(tc, {}) or (gun, saat + boy) in teacher_occ.get(tc, {}):
                s -= 4  # bitisiklik (pencere minimizasyonu icin)
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

    # ---------------- 5. Yerlestirme + displacement ----------------
    MAX_DERINLIK = 3

    def yerlestirmeye_calis(gid, derinlik=0):
        aday = en_iyi_aday(gid)
        if aday:
            yerlestir(gid, aday[0], aday[1])
            return True
        if derinlik >= MAX_DERINLIK:
            return False

        g = gid_map[gid]
        k = tc_kisit.get(g["tc"], {"bosGun": None, "kapali": set(), "maxG": None})
        key = (g["sid"], g["did"])

        for gun in gunler:
            if g["tc"] and (k["bosGun"] == gun or gun in k["kapali"]):
                continue
            if gun_ders.get(key, {}).get(gun, 0) >= 1:
                continue
            if g["tc"] and k["maxG"] and day_load[g["tc"]][gun] + g["boy"] > k["maxG"]:
                continue
            for saat in range(1, gun_bilgi[gun] - g["boy"] + 2):
                cakisanlar = set()
                for b in range(g["boy"]):
                    s = saat + b
                    occ1 = class_occ[g["sid"]].get((gun, s))
                    occ2 = teacher_occ.get(g["tc"], {}).get((gun, s)) if g["tc"] else None
                    if occ1:
                        cakisanlar.add(occ1)
                    if occ2:
                        cakisanlar.add(occ2)
                if not cakisanlar:
                    continue  # bos slot olsaydi en_iyi_aday zaten bulurdu; atla
                if len(cakisanlar) > 2:
                    continue  # cok fazla kovma riskli

                yedek = [(cg, gid_map[cg]["placed"]) for cg in cakisanlar]
                for cg, _ in yedek:
                    bosalt(cg)

                if musait_mi(gid, gun, saat):
                    yerlestir(gid, gun, saat)
                    basarili = True
                    for cg, _ in yedek:
                        if not yerlestirmeye_calis(cg, derinlik + 1):
                            basarili = False
                            break
                    if basarili:
                        return True
                    # geri al
                    bosalt(gid)
                    for cg, eski in yedek:
                        if gid_map[cg]["placed"]:
                            bosalt(cg)
                        yerlestir(cg, eski[0], eski[1])
                else:
                    for cg, eski in yedek:
                        yerlestir(cg, eski[0], eski[1])
        return False

    eksikler_gid = []
    for gid in kuyruk:
        if not yerlestirmeye_calis(gid):
            eksikler_gid.append(gid)

    # ---------------- 6. Min gunluk saat onarimi ----------------
    def min_repair_pass():
        for tc in tum_tc:
            ming = tc_kisit[tc]["minG"]
            if not ming:
                continue
            for gun in gunler:
                if 0 < day_load[tc][gun] < ming:
                    adaylar_tasima = [g for g in gorevler
                                       if g["tc"] == tc and g["placed"] and g["placed"][0] != gun]
                    adaylar_tasima.sort(key=lambda g: -day_load[tc][g["placed"][0]])
                    for t in adaylar_tasima:
                        if day_load[tc][gun] >= ming:
                            break
                        kaynak_gun = t["placed"][0]
                        kalan = day_load[tc][kaynak_gun] - t["boy"]
                        if 0 < kalan < ming:
                            continue  # kaynak gunu de bozar, atla
                        eski = t["placed"]
                        bosalt(t["id"])
                        secenekler = [s for s in adaylar(t["id"]) if s[0] == gun]
                        if secenekler:
                            secenekler.sort(key=lambda gs: skor(t["id"], gs[0], gs[1]))
                            yerlestir(t["id"], secenekler[0][0], secenekler[0][1])
                        else:
                            yerlestir(t["id"], eski[0], eski[1])

    min_repair_pass()

    # ---------------- 7. Bos gun konsolidasyonu ----------------
    def bos_gun_pass():
        for tc in tum_tc:
            yukler = {gun: day_load[tc][gun] for gun in gunler if day_load[tc][gun] > 0}
            if len(yukler) <= 1:
                continue
            hedef_gun = min(yukler, key=yukler.get)
            tasklar = [g for g in gorevler if g["tc"] == tc and g["placed"] and g["placed"][0] == hedef_gun]
            if not tasklar:
                continue
            yedek = [(t["id"], t["placed"]) for t in tasklar]
            for t in tasklar:
                bosalt(t["id"])
            hepsi_tasindi = True
            for t in tasklar:
                aday = en_iyi_aday(t["id"], haric_gun=hedef_gun)
                if aday:
                    yerlestir(t["id"], aday[0], aday[1])
                else:
                    hepsi_tasindi = False
                    break
            if not hepsi_tasindi:
                for t in tasklar:
                    if t["placed"]:
                        bosalt(t["id"])
                for gid, (gun, saat) in yedek:
                    yerlestir(gid, gun, saat)

    bos_gun_pass()

    # ---------------- 8. Pencere minimizasyonu ----------------
    def pencere_pass():
        for tc in tum_tc:
            for gun in gunler:
                for _tur in range(5):
                    tasklar = sorted(
                        [g for g in gorevler if g["tc"] == tc and g["placed"] and g["placed"][0] == gun],
                        key=lambda g: g["placed"][1])
                    if len(tasklar) < 2:
                        break
                    degisti = False
                    for t in tasklar:
                        gun2, saat2 = t["placed"]
                        if saat2 <= 1:
                            continue
                        bosalt(t["id"])
                        if musait_mi(t["id"], gun2, saat2 - 1):
                            yerlestir(t["id"], gun2, saat2 - 1)
                            degisti = True
                        else:
                            yerlestir(t["id"], gun2, saat2)
                    if not degisti:
                        break

    pencere_pass()

    # ---------------- 9. Eksikleri tekrar dene ----------------
    hala_eksik = []
    for gid in eksikler_gid:
        if not yerlestirmeye_calis(gid):
            hala_eksik.append(gid)

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

    eksikler = []
    for gid in hala_eksik:
        g = gid_map[gid]
        eksikler.append({"sinif": siniflar[g["sid"]].get("sinif_adi"),
                          "ders": dersler[g["did"]].get("ders_adi"), "blok": g["boy"]})

    basarili = len(hala_eksik) == 0
    durum = "OPTIMAL" if basarili else "PARTIAL"
    sure = round(time.time() - t0, 2)
    print(f"Tamamlandi {sure}s eksik={len(eksikler)}", flush=True)

    return {"basari": basarili, "slots": slots, "eksikler": eksikler,
            "sure_sn": sure, "durum": durum, "seed": seed}
