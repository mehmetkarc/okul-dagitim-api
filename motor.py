"""
OkulYonetimSistemi - Ders Dagitim Motoru v6
10 Asamali: Yerlesim → MinGun → Kisitlar → BosGun → Pencere → Kontrol → loop
Toplam sure: ~5 dakika
"""
import time, random
from ortools.sat.python import cp_model


# ═══════════════════════════════════════════════════════
# YARDIMCI FONKSİYONLAR
# ═══════════════════════════════════════════════════════

def _ogrt_gun_saatleri(tc, konum, gorevler):
    gs = {}
    for g in gorevler:
        if g["tc"] != tc: continue
        if g["id"] not in konum: continue
        gun, saat = konum[g["id"]]
        if gun not in gs: gs[gun] = set()
        for b in range(g["boy"]):
            gs[gun].add(saat + b)
    return gs


def _pencere_sayisi(saatler_set):
    if len(saatler_set) < 2: return 0
    s = sorted(saatler_set)
    return sum(1 for i in range(s[0], s[-1]) if i not in saatler_set)


def _cakisma_var_mi(g, yeni_gun, yeni_saat, konum, gorevler, kisitlar):
    """Taşıma öncesi çakışma kontrolü."""
    gid = g["id"]
    k = kisitlar.get(g["tc"], {})
    
    # Boş gün ihlali
    if g["tc"] and k.get("bosGun") and int(k["bosGun"]) == yeni_gun:
        return True
    # Kapalı gün
    if g["tc"] and yeni_gun in [int(v) for v in k.get("kapaliGunler", [])]:
        return True
    # Blok-gün kuralı (aynı dersin başka bloğu bu günde)
    for g2 in gorevler:
        if g2["id"] == gid: continue
        if g2["sid"] == g["sid"] and g2["did"] == g["did"] and g2["id"] in konum:
            if konum[g2["id"]][0] == yeni_gun:
                return True
    # Sınıf çakışması
    for g2 in gorevler:
        if g2["id"] == gid: continue
        if g2["sid"] != g["sid"]: continue
        if g2["id"] not in konum: continue
        g2_gun, g2_saat = konum[g2["id"]]
        if g2_gun != yeni_gun: continue
        for b in range(g["boy"]):
            for b2 in range(g2["boy"]):
                if yeni_saat + b == g2_saat + b2:
                    return True
    # Öğretmen çakışması
    if g["tc"]:
        for g2 in gorevler:
            if g2["id"] == gid: continue
            if g2["tc"] != g["tc"]: continue
            if g2["id"] not in konum: continue
            g2_gun, g2_saat = konum[g2["id"]]
            if g2_gun != yeni_gun: continue
            for b in range(g["boy"]):
                for b2 in range(g2["boy"]):
                    if yeni_saat + b == g2_saat + b2:
                        return True
    return False


def _ceza_hesapla_tc(tc, konum, gorevler, kisitlar):
    """Bir öğretmenin toplam cezasını hesaplar."""
    k = kisitlar.get(tc, {})
    min_gun = int(k.get("minGunlukSaat", 2))
    max_gun = int(k.get("maxGunlukSaat", 8))
    gs = _ogrt_gun_saatleri(tc, konum, gorevler)
    ceza = 0
    haftalik_pencere = 0
    for gun, saatler in gs.items():
        n = len(saatler)
        if n == 0: continue
        if n < min_gun: ceza += 1000 * (min_gun - n)   # Günde tek ders — çok ağır
        if n > max_gun: ceza += 800 * (n - max_gun)
        p = _pencere_sayisi(saatler)
        haftalik_pencere += p
    if haftalik_pencere > 2:
        ceza += 300 * (haftalik_pencere - 2)
    return ceza


def _toplam_ceza(konum, gorevler, kisitlar):
    tc_set = set(g["tc"] for g in gorevler if g["tc"])
    return sum(_ceza_hesapla_tc(tc, konum, gorevler, kisitlar) for tc in tc_set)


# ═══════════════════════════════════════════════════════
# ASAMA 1: CP-SAT İLE YERLEŞİM
# ═══════════════════════════════════════════════════════

def asama1_cpsat(gorevler, gid_adaylar, gun_bilgi, gunler, seed, max_sure=180):
    """Tüm dersleri hard kısıtlarla yerleştirir. 3 dakika süre."""
    model = cp_model.CpModel()
    x = {(g["id"], gun, saat): model.NewBoolVar(f"x_{g['id']}_{gun}_{saat}")
         for g in gorevler
         for (gun, saat) in gid_adaylar[g["id"]]}

    # Her görev tam 1 yere
    for g in gorevler:
        av = gid_adaylar[g["id"]]
        if av:
            model.AddExactlyOne([x[(g["id"], gun, saat)] for (gun, saat) in av])

    # Blok-gün
    sid_did = {}
    for g in gorevler: sid_did.setdefault((g["sid"], g["did"]), []).append(g)
    for (sid, did), glist in sid_did.items():
        if len(glist) < 2: continue
        for gun in gunler:
            gv = [x[(g["id"], ag, as_)] for g in glist
                  for (ag, as_) in gid_adaylar[g["id"]] if ag == gun]
            if gv: model.Add(sum(gv) <= 1)

    # Sınıf çakışması
    sid_g = {}
    for g in gorevler: sid_g.setdefault(g["sid"], []).append(g)
    for sid, glist in sid_g.items():
        for gun in gunler:
            for saat in range(1, gun_bilgi[gun] + 1):
                av = [x[(g["id"], ag, as_)] for g in glist
                      for (ag, as_) in gid_adaylar[g["id"]]
                      if ag == gun and as_ <= saat < as_ + g["boy"]]
                if len(av) > 1: model.Add(sum(av) <= 1)

    # Öğretmen çakışması
    tc_g = {}
    for g in gorevler:
        if g["tc"]: tc_g.setdefault(g["tc"], []).append(g)
    for tc, glist in tc_g.items():
        for gun in gunler:
            for saat in range(1, gun_bilgi[gun] + 1):
                av = [x[(g["id"], ag, as_)] for g in glist
                      for (ag, as_) in gid_adaylar[g["id"]]
                      if ag == gun and as_ <= saat < as_ + g["boy"]]
                if len(av) > 1: model.Add(sum(av) <= 1)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max_sure
    solver.parameters.num_workers = 8
    solver.parameters.random_seed = seed
    durum = solver.Solve(model)

    konum = {}
    if durum in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for g in gorevler:
            for (gun, saat) in gid_adaylar[g["id"]]:
                if solver.Value(x[(g["id"], gun, saat)]) == 1:
                    konum[g["id"]] = (gun, saat)
                    break
    return konum, solver.StatusName(durum)


# ═══════════════════════════════════════════════════════
# LOCAL SEARCH OPTİMİZASYON (Asama 2-10)
# ═══════════════════════════════════════════════════════

def local_search(konum, gorevler, gid_adaylar, kisitlar, gun_bilgi, max_sure=120, hedef_ceza=0):
    """
    Simulated Annealing tabanlı local search.
    Pencere, min/max günlük ders kısıtlarını optimize eder.
    Verilen süre dolana veya hedef_ceza'ya ulaşana kadar çalışır.
    """
    t0 = time.time()
    konum = dict(konum)
    
    tc_list = list(set(g["tc"] for g in gorevler if g["tc"]))
    tc_gorevler = {tc: [g for g in gorevler if g["tc"] == tc] for tc in tc_list}
    
    mevcut_ceza = _toplam_ceza(konum, gorevler, kisitlar)
    en_iyi_ceza = mevcut_ceza
    en_iyi_konum = dict(konum)
    
    # Simulated Annealing parametreleri
    sicaklik = 500.0
    soguma = 0.9995
    iterasyon = 0
    
    while time.time() - t0 < max_sure:
        if mevcut_ceza <= hedef_ceza:
            break
        iterasyon += 1
        
        # Önce cezalı öğretmenlere odaklan, sonra rastgele
        if iterasyon % 10 < 7:
            # Cezalı öğretmeni seç
            cezali = [(tc, _ceza_hesapla_tc(tc, konum, gorevler, kisitlar))
                      for tc in tc_list]
            cezali = [(tc, c) for tc, c in cezali if c > 0]
            if not cezali:
                tc = random.choice(tc_list)
            else:
                # Ağırlıklı seçim: daha çok cezalı olan daha sık seçilir
                total = sum(c for _, c in cezali)
                r = random.random() * total
                cum = 0
                tc = cezali[0][0]
                for t, c in cezali:
                    cum += c
                    if r <= cum:
                        tc = t
                        break
        else:
            tc = random.choice(tc_list)
        
        glist = tc_gorevler.get(tc, [])
        if not glist: continue
        
        g = random.choice(glist)
        gid = g["id"]
        adaylar = gid_adaylar[gid]
        if len(adaylar) < 2: continue
        
        eski_konum = konum.get(gid)
        if not eski_konum: continue
        
        # Yeni konum: önce iyi konumları dene
        yeni_aday = random.choice(adaylar)
        if yeni_aday == eski_konum: continue
        
        # Çakışma kontrolü
        konum_test = dict(konum)
        del konum_test[gid]
        
        yeni_gun, yeni_saat = yeni_aday
        if _cakisma_var_mi(g, yeni_gun, yeni_saat, konum_test, gorevler, kisitlar):
            continue
        
        # Ceza karşılaştır
        ceza_eski = _ceza_hesapla_tc(tc, konum, gorevler, kisitlar)
        
        konum[gid] = yeni_aday
        ceza_yeni = _ceza_hesapla_tc(tc, konum, gorevler, kisitlar)
        
        delta = ceza_yeni - ceza_eski
        
        if delta <= 0:
            # İyileşme — kabul et
            mevcut_ceza += delta
        elif sicaklik > 0.1 and random.random() < pow(2.718, -delta / sicaklik):
            # SA: bazen kötü hamleyi de kabul et (yerel minimumdan kaç)
            mevcut_ceza += delta
        else:
            # Geri al
            konum[gid] = eski_konum
        
        if mevcut_ceza < en_iyi_ceza:
            en_iyi_ceza = mevcut_ceza
            en_iyi_konum = dict(konum)
        
        sicaklik *= soguma
    
    print(f"  Local search: {iterasyon} iter, ceza {_toplam_ceza(en_iyi_konum, gorevler, kisitlar)}, sure {round(time.time()-t0,1)}s")
    return en_iyi_konum, en_iyi_ceza


# ═══════════════════════════════════════════════════════
# ANA FONKSİYON
# ═══════════════════════════════════════════════════════

def dagit(veri):
    t0 = time.time()
    siniflar  = {str(s["id"]): s for s in veri.get("siniflar", [])}
    dersler   = {str(d["id"]): d for d in veri.get("dersler", [])}
    atamalar  = {str(k): v for k, v in veri.get("atamalar", {}).items()}
    kisitlar  = {str(k): v for k, v in veri.get("kisitlar", {}).items()}
    gun_bilgi = {int(g["gun"]): int(g["saat"]) for g in veri.get("gunler", [])}
    kilitli   = veri.get("kilitli", {})
    gunler    = sorted(gun_bilgi.keys())
    seed      = veri.get("seed", random.randint(1, 999999))
    random.seed(seed)

    # Görev listesi
    gorevler = []
    for sid, atama_list in atamalar.items():
        if sid not in siniflar: continue
        for atama in atama_list:
            did = str(atama.get("ders_id", ""))
            if did not in dersler: continue
            ders = dersler[did]
            tc = str(atama.get("ogretmen_tc") or
                     (atama.get("ogretmenler") or [{}])[0].get("tc") or "")
            bloklar = ders.get("blok_dagilim") or [ders.get("haftalik_saat", 1)]
            for bi, boy in enumerate(bloklar):
                if not boy: continue
                gorevler.append({"id": f"{sid}_{did}_{bi}", "sid": sid, "did": did,
                                 "tc": tc, "ogrtler": atama.get("ogretmenler", []),
                                 "boy": int(boy), "bi": bi})

    if not gorevler:
        return {"basari": True, "slots": {sid: {} for sid in siniflar},
                "eksikler": [], "sure_sn": 0, "durum": "EMPTY"}

    # Aday konumlar (boş gün / kapalı gün filtreli)
    gid_adaylar = {}
    for g in gorevler:
        gid = g["id"]
        gid_adaylar[gid] = []
        k = kisitlar.get(g["tc"], {})
        bos_gun = int(k["bosGun"]) if k.get("bosGun") else None
        kapali  = [int(v) for v in k.get("kapaliGunler", [])]
        for gun in gunler:
            if bos_gun and gun == bos_gun: continue
            if gun in kapali: continue
            for saat in range(1, gun_bilgi[gun] - g["boy"] + 2):
                gid_adaylar[gid].append((gun, saat))

    # ── ASAMA 1: CP-SAT yerleşim (3 dakika) ──────────────────────
    print("Asama 1: CP-SAT yerlesim basliyor...")
    konum, cp_durum = asama1_cpsat(gorevler, gid_adaylar, gun_bilgi, gunler, seed, max_sure=180)
    
    if not konum:
        return {"basari": False, "slots": {sid: {} for sid in siniflar},
                "eksikler": [{"sinif": "?", "ders": "CP-SAT cozum bulamadi", "blok": 0}],
                "sure_sn": round(time.time()-t0, 2), "durum": cp_durum}

    print(f"  CP-SAT: {cp_durum}, {len(konum)}/{len(gorevler)} gorev, {round(time.time()-t0,1)}s")

    # ── ASAMA 2-10: Local Search döngüsü (kalan süre: ~2 dakika) ──
    kalan_sure = 290 - (time.time() - t0)  # Toplam 5dk - gecen sure
    
    print(f"Asama 2-10: Local search optimizasyon ({round(kalan_sure)}s)...")
    baslangic_ceza = _toplam_ceza(konum, gorevler, kisitlar)
    print(f"  Baslangic cezasi: {baslangic_ceza}")
    # Debug: ilk 3 ogretmenin kisitlarini logla
    ornek_tc = list(set(g["tc"] for g in gorevler if g["tc"]))[:3]
    for tc in ornek_tc:
        k = kisitlar.get(tc, {})
        gs = _ogrt_gun_saatleri(tc, konum, gorevler)
        gun_saatleri = {gun: len(s) for gun, s in gs.items() if s}
        print(f"  [{tc[:8]}] kisit:{k.get('minGunlukSaat','?')}-{k.get('maxGunlukSaat','?')} gun_saatleri:{gun_saatleri}")
    
    # Birden fazla tur: her turda daha soğuk SA, daha detaylı optimizasyon
    TUR_SURE = kalan_sure / 3  # 3 tur
    
    for tur in range(3):
        print(f"  Tur {tur+1}/3...")
        konum, son_ceza = local_search(
            konum, gorevler, gid_adaylar, kisitlar, gun_bilgi,
            max_sure=TUR_SURE, hedef_ceza=0
        )
        if son_ceza == 0:
            print(f"  Hedef ceza 0'a ulasild! Tur {tur+1}'de bitti.")
            break

    # ── Sonucu slots formatına çevir ─────────────────────────────
    slots = {sid: {} for sid in siniflar}
    eksikler = []
    for g in gorevler:
        gid = g["id"]
        if gid not in konum:
            eksikler.append({"sinif": siniflar[g["sid"]].get("sinif_adi"),
                             "ders": dersler[g["did"]].get("ders_adi"),
                             "blok": g["boy"]})
            continue
        gun, saat = konum[gid]
        sid = g["sid"]
        ders = dersler[g["did"]]
        if gun not in slots[sid]: slots[sid][gun] = {}
        for b in range(g["boy"]):
            slots[sid][gun][saat + b] = {
                "ders_id": g["did"], "ders_adi": ders.get("ders_adi", ""),
                "kisa_ad": ders.get("kisa_ad", ders.get("ders_adi", "")[:4]),
                "renk": ders.get("renk", "#1a6b47"), "ogretmen_tc": g["tc"],
                "ogretmenler": g["ogrtler"], "kilitli": False
            }

    sure = time.time() - t0
    print(f"Tamamlandi: {round(sure,1)}s, Eksik:{len(eksikler)}, SonCeza:{son_ceza}")
    return {"basari": True, "slots": slots, "eksikler": eksikler,
            "sure_sn": round(sure, 2), "durum": cp_durum, "seed": seed}


if __name__ == "__main__":
    test = {
        "siniflar": [{"id": str(i), "sinif_adi": f"9-{chr(64+i)}"} for i in range(1, 4)],
        "dersler": [
            {"id": "101", "ders_adi": "Mat", "haftalik_saat": 4, "blok_dagilim": [2,2], "renk": "#2563eb", "kisa_ad": "MAT"},
            {"id": "102", "ders_adi": "Tur", "haftalik_saat": 5, "blok_dagilim": [2,2,1], "renk": "#dc2626", "kisa_ad": "TUR"},
            {"id": "103", "ders_adi": "Bed", "haftalik_saat": 2, "blok_dagilim": [2], "renk": "#16a34a", "kisa_ad": "BED"},
        ],
        "atamalar": {str(i): [
            {"ders_id": "101", "ogretmen_tc": "TC001", "ogretmenler": [{"tc": "TC001"}]},
            {"ders_id": "102", "ogretmen_tc": "TC002", "ogretmenler": [{"tc": "TC002"}]},
            {"ders_id": "103", "ogretmen_tc": "TC003", "ogretmenler": [{"tc": "TC003"}]},
        ] for i in range(1, 4)},
        "kisitlar": {"TC001": {"minGunlukSaat": 2, "maxGunlukSaat": 6}, "TC002": {"minGunlukSaat": 2}},
        "gunler": [{"gun": i, "saat": 8} for i in range(1, 6)],
        "kilitli": {}
    }
    import json
    r = dagit(test)
    print(json.dumps({"durum": r["durum"], "sure": r["sure_sn"], "eksik": len(r["eksikler"])}, ensure_ascii=False))
