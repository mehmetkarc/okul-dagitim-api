"""
OkulYonetimSistemi - Ders Dagitim Motoru v11
Saf SA: rastgele baslangic + cakisma cezasi + pencere/minmax cezasi.
CP-SAT yok, model kurma overhead yok. 5 dakika tam optimize.
"""
import time, random, sys


def dagit(veri):
    t0 = time.time()
    siniflar  = {str(s["id"]): s for s in veri.get("siniflar", [])}
    dersler   = {str(d["id"]): d for d in veri.get("dersler", [])}
    atamalar  = {str(k): v for k, v in veri.get("atamalar", {}).items()}
    kisitlar  = {str(k): v for k, v in veri.get("kisitlar", {}).items()}
    gun_bilgi = {int(g["gun"]): int(g["saat"]) for g in veri.get("gunler", [])}
    gunler    = sorted(gun_bilgi.keys())
    seed      = veri.get("seed", random.randint(1, 999999))
    random.seed(seed)

    gorevler = []
    for sid, atama_list in atamalar.items():
        if sid not in siniflar: continue
        for atama in atama_list:
            did = str(atama.get("ders_id", ""))
            if did not in dersler: continue
            ders = dersler[did]
            tc = str(atama.get("ogretmen_tc") or (atama.get("ogretmenler") or [{}])[0].get("tc") or "")
            bloklar = ders.get("blok_dagilim") or [ders.get("haftalik_saat", 1)]
            for bi, boy in enumerate(bloklar):
                if not boy: continue
                gorevler.append({"id": f"{sid}_{did}_{bi}", "sid": sid, "did": did,
                                 "tc": tc, "ogrtler": atama.get("ogretmenler", []),
                                 "boy": int(boy), "bi": bi})

    if not gorevler:
        return {"basari": True, "slots": {sid: {} for sid in siniflar},
                "eksikler": [], "sure_sn": 0, "durum": "EMPTY"}

    print(f"Gorev:{len(gorevler)} Sinif:{len(siniflar)}", flush=True)

    # Aday konumlar
    gid_adaylar = {}
    for g in gorevler:
        gid = g["id"]
        gid_adaylar[gid] = []
        k = kisitlar.get(g["tc"], {})
        bos_gun = int(k["bosGun"]) if k.get("bosGun") else None
        kapali = [int(v) for v in k.get("kapaliGunler", [])]
        for gun in gunler:
            if bos_gun and gun == bos_gun: continue
            if gun in kapali: continue
            for saat in range(1, gun_bilgi[gun] - g["boy"] + 2):
                gid_adaylar[gid].append((gun, saat))

    # Hızlı lookup yapıları
    sinif_saat = {}   # (sid, gun, saat) -> gid
    ogrt_saat  = {}   # (tc, gun, saat)  -> gid
    did_gun    = {}   # (sid, did, gun)   -> count

    def _ekle(g, gun, saat):
        for b in range(g["boy"]):
            sinif_saat[(g["sid"], gun, saat+b)] = g["id"]
            if g["tc"]: ogrt_saat[(g["tc"], gun, saat+b)] = g["id"]
        k = (g["sid"], g["did"], gun)
        did_gun[k] = did_gun.get(k, 0) + 1

    def _kaldir(g, gun, saat):
        for b in range(g["boy"]):
            sinif_saat.pop((g["sid"], gun, saat+b), None)
            if g["tc"]: ogrt_saat.pop((g["tc"], gun, saat+b), None)
        k = (g["sid"], g["did"], gun)
        did_gun[k] = did_gun.get(k, 1) - 1
        if did_gun[k] <= 0: del did_gun[k]

    # Ceza fonksiyonu - tek bir görev için
    def ceza_gorev(g, gun, saat):
        ceza = 0
        # Çakışmalar (hard)
        for b in range(g["boy"]):
            mevcut_s = sinif_saat.get((g["sid"], gun, saat+b))
            if mevcut_s and mevcut_s != g["id"]: ceza += 2000
            if g["tc"]:
                mevcut_o = ogrt_saat.get((g["tc"], gun, saat+b))
                if mevcut_o and mevcut_o != g["id"]: ceza += 2000
        # Blok-gün (hard)
        k = (g["sid"], g["did"], gun)
        mevcut_bg = did_gun.get(k, 0)
        if mevcut_bg > 0: ceza += 3000
        return ceza

    # Öğretmen günlük ceza
    def ceza_tc_gun(tc, gun, konum, glist):
        k = kisitlar.get(tc, {})
        min_g = int(k.get("minGunlukSaat", 2))
        max_g = int(k.get("maxGunlukSaat", 8))
        saatler = set()
        for g in glist:
            if g["id"] not in konum: continue
            gg, gs = konum[g["id"]]
            if gg != gun: continue
            for b in range(g["boy"]): saatler.add(gs+b)
        n = len(saatler)
        ceza = 0
        if n == 0: return 0
        if n < min_g: ceza += 500 * (min_g - n)
        if n > max_g: ceza += 500 * (n - max_g)
        if n >= 2:
            sr = sorted(saatler)
            pen = sum(1 for i in range(sr[0], sr[-1]) if i not in saatler)
            if pen > 0: ceza += 150 * pen
        return ceza

    # Rastgele başlangıç yerleşimi
    konum = {}
    for g in gorevler:
        adaylar = gid_adaylar[g["id"]]
        if adaylar:
            gun, saat = random.choice(adaylar)
            konum[g["id"]] = (gun, saat)
            _ekle(g, gun, saat)

    tc_g = {}
    for g in gorevler:
        if g["tc"]: tc_g.setdefault(g["tc"], []).append(g)

    # Toplam cezayı hesapla
    def toplam_ceza_hesapla():
        c = 0
        for gid, (gun, saat) in konum.items():
            g = next(gg for gg in gorevler if gg["id"] == gid)
            c += ceza_gorev(g, gun, saat)
        for tc, glist in tc_g.items():
            for gun in gunler:
                c += ceza_tc_gun(tc, gun, konum, glist)
        return c

    toplam = toplam_ceza_hesapla()
    en_iyi = toplam
    en_iyi_konum = dict(konum)

    print(f"SA baslangic ceza:{toplam}", flush=True)

    sicaklik = 5000.0
    soguma = 0.99998
    iterasyon = 0
    son_log = time.time()
    gorev_listesi = list(gorevler)

    while time.time() - t0 < 300:
        iterasyon += 1
        if time.time() - son_log > 20:
            print(f"SA iter={iterasyon} ceza={toplam} T={sicaklik:.1f}", flush=True)
            son_log = time.time()
        if toplam == 0: break

        # Rastgele görev seç - cezalıya öncelik ver
        if random.random() < 0.7:
            # Çakışması veya cezası olan görev seç
            g = random.choice(gorev_listesi)
            gun0, saat0 = konum.get(g["id"], (0,0))
            if gun0 and ceza_gorev(g, gun0, saat0) == 0:
                # Rastgele başka biri
                g = random.choice(gorev_listesi)
        else:
            g = random.choice(gorev_listesi)

        if g["id"] not in konum: continue
        eg, es = konum[g["id"]]
        adaylar = gid_adaylar[g["id"]]
        if len(adaylar) < 2: continue
        yg, ys = random.choice(adaylar)
        if (yg, ys) == (eg, es): continue

        # Ceza farkını hesapla
        _kaldir(g, eg, es)
        eski_c = ceza_gorev(g, eg, es)  # eski pozisyondaki ceza (zaten kaldırıldı, 0 olur)
        yeni_c = ceza_gorev(g, yg, ys)

        # TC günlük ceza farkı
        tc = g["tc"]
        if tc:
            tc_eski_eg = ceza_tc_gun(tc, eg, konum, tc_g[tc])
            tc_eski_yg = ceza_tc_gun(tc, yg, konum, tc_g[tc])
            konum[g["id"]] = (yg, ys)
            _ekle(g, yg, ys)
            tc_yeni_eg = ceza_tc_gun(tc, eg, konum, tc_g[tc])
            tc_yeni_yg = ceza_tc_gun(tc, yg, konum, tc_g[tc])
            delta = (yeni_c - eski_c) + (tc_yeni_eg - tc_eski_eg) + (tc_yeni_yg - tc_eski_yg)
        else:
            konum[g["id"]] = (yg, ys)
            _ekle(g, yg, ys)
            delta = yeni_c - eski_c

        if delta <= 0 or (sicaklik > 0.1 and random.random() < pow(2.718, -delta/sicaklik)):
            toplam += delta
            if toplam < en_iyi:
                en_iyi = toplam
                en_iyi_konum = dict(konum)
        else:
            # Geri al
            _kaldir(g, yg, ys)
            konum[g["id"]] = (eg, es)
            _ekle(g, eg, es)

        sicaklik *= soguma

    print(f"SA bitti:{iterasyon} iter en_iyi={en_iyi}", flush=True)

    # En iyi sonucu uygula
    # Lookup'ı sıfırla ve en iyi konumu kur
    sinif_saat.clear(); ogrt_saat.clear(); did_gun.clear()
    for g in gorevler:
        if g["id"] in en_iyi_konum:
            gun, saat = en_iyi_konum[g["id"]]
            _ekle(g, gun, saat)

    # Sonucu yaz
    slots = {sid: {} for sid in siniflar}
    eksikler = []
    for g in gorevler:
        if g["id"] not in en_iyi_konum:
            eksikler.append({"sinif": siniflar[g["sid"]].get("sinif_adi"),
                             "ders": dersler[g["did"]].get("ders_adi"), "blok": g["boy"]}); continue
        gun, saat = en_iyi_konum[g["id"]]
        # Çakışma kontrolü - hâlâ çakışma varsa eksik say
        cakisma = False
        for b in range(g["boy"]):
            occ = sinif_saat.get((g["sid"], gun, saat+b))
            if occ and occ != g["id"]: cakisma=True; break
            if g["tc"]:
                occ2 = ogrt_saat.get((g["tc"], gun, saat+b))
                if occ2 and occ2 != g["id"]: cakisma=True; break
        if cakisma:
            eksikler.append({"sinif": siniflar[g["sid"]].get("sinif_adi"),
                             "ders": dersler[g["did"]].get("ders_adi"), "blok": g["boy"]}); continue

        sid = g["sid"]; ders = dersler[g["did"]]
        if gun not in slots[sid]: slots[sid][gun] = {}
        for b in range(g["boy"]):
            slots[sid][gun][saat+b] = {"ders_id": g["did"], "ders_adi": ders.get("ders_adi",""),
                "kisa_ad": ders.get("kisa_ad", ders.get("ders_adi","")[:4]),
                "renk": ders.get("renk","#1a6b47"), "ogretmen_tc": g["tc"],
                "ogretmenler": g["ogrtler"], "kilitli": False}

    sure = time.time()-t0
    print(f"Tamamlandi {round(sure,1)}s eksik={len(eksikler)}", flush=True)
    return {"basari": en_iyi==0, "slots": slots, "eksikler": eksikler,
            "sure_sn": round(sure,2), "durum": "SA_OPTIMAL" if en_iyi==0 else "SA_FEASIBLE", "seed": seed}


if __name__ == "__main__":
    test = {
        "siniflar": [{"id": str(i), "sinif_adi": f"9-{i}"} for i in range(1,4)],
        "dersler": [{"id":"101","ders_adi":"Mat","haftalik_saat":4,"blok_dagilim":[2,2],"renk":"#2563eb","kisa_ad":"MAT"},
                    {"id":"102","ders_adi":"Tur","haftalik_saat":5,"blok_dagilim":[2,2,1],"renk":"#dc2626","kisa_ad":"TUR"}],
        "atamalar": {str(i):[{"ders_id":"101","ogretmen_tc":"TC001","ogretmenler":[{"tc":"TC001"}]},
                              {"ders_id":"102","ogretmen_tc":"TC002","ogretmenler":[{"tc":"TC002"}]}] for i in range(1,4)},
        "kisitlar": {"TC001":{"minGunlukSaat":2,"maxGunlukSaat":6},"TC002":{"minGunlukSaat":2,"maxGunlukSaat":6}},
        "gunler": [{"gun":i,"saat":8} for i in range(1,6)], "kilitli":{}
    }
    r = dagit(test)
    print(f"Durum:{r['durum']} Sure:{r['sure_sn']}s Eksik:{len(r['eksikler'])}")
