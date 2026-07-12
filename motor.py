"""
OkulYonetimSistemi - Ders Dagitim Motoru v12
CP-SAT SADECE hard kisitlar - onceki calisir versiyona donus.
Pencere/mingün optimizasyonu sonraki aşamada (ayrı endpoint).
"""
import time, random
from ortools.sat.python import cp_model


def dagit(veri):
    t0 = time.time()
    siniflar  = {str(s["id"]): s for s in veri.get("siniflar", [])}
    dersler   = {str(d["id"]): d for d in veri.get("dersler", [])}
    atamalar  = {str(k): v for k, v in veri.get("atamalar", {}).items()}
    kisitlar  = {str(k): v for k, v in veri.get("kisitlar", {}).items()}
    gun_bilgi = {int(g["gun"]): int(g["saat"]) for g in veri.get("gunler", [])}
    gunler    = sorted(gun_bilgi.keys())
    seed      = veri.get("seed", random.randint(1, 999999))

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

    model = cp_model.CpModel()
    x = {(g["id"], gun, saat): model.NewBoolVar(f"x_{g['id']}_{gun}_{saat}")
         for g in gorevler for (gun, saat) in gid_adaylar[g["id"]]}

    # Hard: her görev tam 1 yere
    for g in gorevler:
        av = [x[(g["id"], gun, saat)] for (gun, saat) in gid_adaylar[g["id"]]]
        if av: model.AddExactlyOne(av)

    # Hard: blok-gün
    sid_did = {}
    for g in gorevler: sid_did.setdefault((g["sid"], g["did"]), []).append(g)
    for (sid, did), glist in sid_did.items():
        if len(glist) < 2: continue
        for gun in gunler:
            gv = [x[(g["id"], ag, as_)] for g in glist
                  for (ag, as_) in gid_adaylar[g["id"]] if ag == gun]
            if gv: model.Add(sum(gv) <= 1)

    # Hard: sınıf çakışması
    sid_g = {}
    for g in gorevler: sid_g.setdefault(g["sid"], []).append(g)
    for sid, glist in sid_g.items():
        for gun in gunler:
            for saat in range(1, gun_bilgi[gun] + 1):
                av = [x[(g["id"], ag, as_)] for g in glist
                      for (ag, as_) in gid_adaylar[g["id"]]
                      if ag == gun and as_ <= saat < as_ + g["boy"]]
                if len(av) > 1: model.Add(sum(av) <= 1)

    # Hard: öğretmen çakışması
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
    solver.parameters.max_time_in_seconds = 55.0
    solver.parameters.num_workers = 8
    solver.parameters.random_seed = seed
    solver.parameters.log_search_progress = False
    durum = solver.Solve(model)
    sure = time.time() - t0
    print(f"CP-SAT:{solver.StatusName(durum)} {round(sure,1)}s", flush=True)

    basarili = durum in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    slots = {sid: {} for sid in siniflar}
    eksikler = []

    if basarili:
        for g in gorevler:
            yerlesik = False
            for (gun, saat) in gid_adaylar[g["id"]]:
                if solver.Value(x[(g["id"], gun, saat)]) == 1:
                    sid = g["sid"]; ders = dersler[g["did"]]
                    if gun not in slots[sid]: slots[sid][gun] = {}
                    for b in range(g["boy"]):
                        slots[sid][gun][saat+b] = {
                            "ders_id": g["did"], "ders_adi": ders.get("ders_adi",""),
                            "kisa_ad": ders.get("kisa_ad", ders.get("ders_adi","")[:4]),
                            "renk": ders.get("renk","#1a6b47"), "ogretmen_tc": g["tc"],
                            "ogretmenler": g["ogrtler"], "kilitli": False}
                    yerlesik = True; break
            if not yerlesik:
                eksikler.append({"sinif": siniflar[g["sid"]].get("sinif_adi"),
                                 "ders": dersler[g["did"]].get("ders_adi"), "blok": g["boy"]})

    # ── ASAMA 2: SA ile min/max günlük kısıt optimizasyonu ─────
    if basarili and eksikler == []:
        kalan = 260 - (time.time() - t0)
        print(f"SA minmax optimizasyon: {round(kalan)}s", flush=True)
        
        # Mevcut konumu al
        konum = {}
        for g in gorevler:
            for (gun, saat) in gid_adaylar[g["id"]]:
                if solver.Value(x[(g["id"], gun, saat)]) == 1:
                    konum[g["id"]] = (gun, saat); break
        
        # Hızlı lookup
        sinif_saat = {}
        ogrt_saat = {}
        did_gun = {}
        
        def _ekle(g, gun, saat):
            for b in range(g["boy"]):
                sinif_saat[(g["sid"], gun, saat+b)] = g["id"]
                if g["tc"]: ogrt_saat[(g["tc"], gun, saat+b)] = g["id"]
            k2 = (g["sid"], g["did"], gun)
            did_gun[k2] = did_gun.get(k2, 0) + 1
        
        def _kaldir(g, gun, saat):
            for b in range(g["boy"]):
                sinif_saat.pop((g["sid"], gun, saat+b), None)
                if g["tc"]: ogrt_saat.pop((g["tc"], gun, saat+b), None)
            k2 = (g["sid"], g["did"], gun)
            did_gun[k2] = did_gun.get(k2, 1) - 1
            if did_gun.get(k2, 0) <= 0: did_gun.pop(k2, None)
        
        def _ok(g, gun, saat):
            k2 = kisitlar.get(g["tc"], {})
            if g["tc"] and k2.get("bosGun") and int(k2["bosGun"]) == gun: return False
            if g["tc"] and gun in [int(v) for v in k2.get("kapaliGunler", [])]: return False
            ms = gun_bilgi.get(gun, 8)
            if saat < 1 or saat + g["boy"] - 1 > ms: return False
            if did_gun.get((g["sid"], g["did"], gun), 0) > 0: return False
            for b in range(g["boy"]):
                if (g["sid"], gun, saat+b) in sinif_saat: return False
                if g["tc"] and (g["tc"], gun, saat+b) in ogrt_saat: return False
            return True
        
        for g in gorevler:
            if g["id"] in konum: _ekle(g, *konum[g["id"]])
        
        def _ceza_tc(tc, glist2):
            k2 = kisitlar.get(tc, {})
            min_g = int(k2.get("minGunlukSaat", 2))
            max_g = int(k2.get("maxGunlukSaat", 8))
            gs = {}
            for g in glist2:
                if g["id"] not in konum: continue
                gun2, saat2 = konum[g["id"]]
                if gun2 not in gs: gs[gun2] = set()
                for b in range(g["boy"]): gs[gun2].add(saat2+b)
            ceza2 = 0
            hpen = 0
            for gun2, s2 in gs.items():
                n = len(s2)
                if n < min_g: ceza2 += 500 * (min_g - n)
                if n > max_g: ceza2 += 500 * (n - max_g)
                if n >= 2:
                    sr = sorted(s2)
                    pen = sum(1 for i in range(sr[0], sr[-1]) if i not in s2)
                    hpen += pen
            if hpen > 2: ceza2 += 200 * (hpen - 2)
            return ceza2
        
        tc_ceza = {tc: _ceza_tc(tc, glist2) for tc, glist2 in tc_g.items()}
        toplam_c = sum(tc_ceza.values())
        en_iyi_c = toplam_c
        en_iyi_k = dict(konum)
        tc_list2 = list(tc_g.keys())
        sicaklik = 2000.0
        soguma = 0.99999
        iter2 = 0
        son_log2 = time.time()
        t_sa = time.time()
        
        print(f"SA baslangic ceza:{toplam_c}", flush=True)
        
        while time.time() - t_sa < kalan:
            iter2 += 1
            if time.time() - son_log2 > 25:
                print(f"SA iter={iter2} ceza={toplam_c} T={sicaklik:.1f}", flush=True)
                son_log2 = time.time()
            if toplam_c == 0: break
            
            cezali = [(tc2, c2) for tc2, c2 in tc_ceza.items() if c2 > 0]
            if random.random() < 0.8 and cezali:
                total_c2 = sum(c2 for _, c2 in cezali)
                r2 = random.random() * total_c2
                cum2 = 0; tc2 = cezali[0][0]
                for t2_, c2 in cezali:
                    cum2 += c2
                    if r2 <= cum2: tc2 = t2_; break
            else:
                tc2 = random.choice(tc_list2)
            
            glist3 = tc_g[tc2]
            g2 = random.choice(glist3)
            if g2["id"] not in konum: continue
            eg2, es2 = konum[g2["id"]]
            adaylar2 = gid_adaylar[g2["id"]]
            if len(adaylar2) < 2: continue
            yg2, ys2 = random.choice(adaylar2)
            if (yg2, ys2) == (eg2, es2): continue
            
            _kaldir(g2, eg2, es2)
            if not _ok(g2, yg2, ys2):
                _ekle(g2, eg2, es2); continue
            
            eski_c2 = tc_ceza[tc2]
            konum[g2["id"]] = (yg2, ys2)
            _ekle(g2, yg2, ys2)
            yeni_c2 = _ceza_tc(tc2, glist3)
            delta2 = yeni_c2 - eski_c2
            
            if delta2 <= 0 or (sicaklik > 0.1 and random.random() < pow(2.718, -delta2/sicaklik)):
                tc_ceza[tc2] = yeni_c2
                toplam_c += delta2
                if toplam_c < en_iyi_c:
                    en_iyi_c = toplam_c
                    en_iyi_k = dict(konum)
            else:
                konum[g2["id"]] = (eg2, es2)
                _kaldir(g2, yg2, ys2)
                _ekle(g2, eg2, es2)
            
            sicaklik *= soguma
        
        print(f"SA bitti:{iter2} iter en_iyi={en_iyi_c}", flush=True)
        
        # En iyi konumu slots'a yaz
        if en_iyi_c < sum(_ceza_tc(tc2, glist2) for tc2, glist2 in tc_g.items()):
            slots = {sid: {} for sid in siniflar}
            for g in gorevler:
                if g["id"] not in en_iyi_k: continue
                gun3, saat3 = en_iyi_k[g["id"]]
                sid3 = g["sid"]; ders3 = dersler[g["did"]]
                if gun3 not in slots[sid3]: slots[sid3][gun3] = {}
                for b in range(g["boy"]):
                    slots[sid3][gun3][saat3+b] = {
                        "ders_id": g["did"], "ders_adi": ders3.get("ders_adi",""),
                        "kisa_ad": ders3.get("kisa_ad", ders3.get("ders_adi","")[:4]),
                        "renk": ders3.get("renk","#1a6b47"), "ogretmen_tc": g["tc"],
                        "ogretmenler": g["ogrtler"], "kilitli": False}

    print(f"Tamamlandi {round(time.time()-t0,1)}s eksik={len(eksikler)}", flush=True)
    return {"basari": basarili, "slots": slots, "eksikler": eksikler,
            "sure_sn": round(time.time()-t0, 2), "durum": solver.StatusName(durum), "seed": seed}


if __name__ == "__main__":
    test = {"siniflar":[{"id":str(i),"sinif_adi":f"9-{i}"} for i in range(1,4)],
            "dersler":[{"id":"101","ders_adi":"Mat","haftalik_saat":4,"blok_dagilim":[2,2],"renk":"#2563eb","kisa_ad":"MAT"},
                       {"id":"102","ders_adi":"Tur","haftalik_saat":5,"blok_dagilim":[2,2,1],"renk":"#dc2626","kisa_ad":"TUR"}],
            "atamalar":{str(i):[{"ders_id":"101","ogretmen_tc":"TC001","ogretmenler":[{"tc":"TC001"}]},
                                  {"ders_id":"102","ogretmen_tc":"TC002","ogretmenler":[{"tc":"TC002"}]}] for i in range(1,4)},
            "kisitlar":{},"gunler":[{"gun":i,"saat":8} for i in range(1,6)],"kilitli":{}}
    r = dagit(test)
    print(f"Durum:{r['durum']} Sure:{r['sure_sn']}s Eksik:{len(r['eksikler'])}")
