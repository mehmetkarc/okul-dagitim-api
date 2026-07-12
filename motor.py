"""
OkulYonetimSistemi - Ders Dagitim Motoru v8
CP-SAT ile hem yerlesim hem min/max gunluk hem pencere optimizasyonu.
SA ile post-processing pencere azaltma.
"""
import time, random, sys
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

    print(f"Asama1: {len(gorevler)} gorev, {len(siniflar)} sinif", flush=True)

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

    # ── CP-SAT MODELİ ────────────────────────────────────────────
    model = cp_model.CpModel()

    # Karar değişkenleri
    x = {}
    for g in gorevler:
        for (gun, saat) in gid_adaylar[g["id"]]:
            x[(g["id"], gun, saat)] = model.NewBoolVar(f"x_{g['id']}_{gun}_{saat}")

    # Hard: Her görev tam 1 yere
    for g in gorevler:
        av = [x[(g["id"], gun, saat)] for (gun, saat) in gid_adaylar[g["id"]]]
        if av: model.AddExactlyOne(av)

    # Hard: Blok-gün kuralı
    sid_did = {}
    for g in gorevler: sid_did.setdefault((g["sid"], g["did"]), []).append(g)
    for (sid, did), glist in sid_did.items():
        if len(glist) < 2: continue
        for gun in gunler:
            gv = [x[(g["id"], ag, as_)] for g in glist
                  for (ag, as_) in gid_adaylar[g["id"]] if ag == gun]
            if gv: model.Add(sum(gv) <= 1)

    # Hard: Sınıf çakışması
    sid_g = {}
    for g in gorevler: sid_g.setdefault(g["sid"], []).append(g)
    for sid, glist in sid_g.items():
        for gun in gunler:
            for saat in range(1, gun_bilgi[gun] + 1):
                av = [x[(g["id"], ag, as_)] for g in glist
                      for (ag, as_) in gid_adaylar[g["id"]]
                      if ag == gun and as_ <= saat < as_ + g["boy"]]
                if len(av) > 1: model.Add(sum(av) <= 1)

    # Hard: Öğretmen çakışması
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

    # ── SOFT KISITLAR (ceza ile optimize) ────────────────────────
    cezalar = []

    for tc, glist in tc_g.items():
        k = kisitlar.get(tc, {})
        min_gun = int(k.get("minGunlukSaat", 2))
        max_gun = int(k.get("maxGunlukSaat", 8))
        max_s_genel = max(gun_bilgi.values())

        for gun in gunler:
            # Bu günde öğretmenin toplam saati
            gun_saat_vars = []
            for g in glist:
                for (ag, as_) in gid_adaylar[g["id"]]:
                    if ag == gun:
                        # Bu blok boy kadar saat kaplar
                        gun_saat_vars.extend([x[(g["id"], ag, as_)]] * g["boy"])

            if not gun_saat_vars: continue

            toplam = sum(gun_saat_vars)

            # Hard MAX: günde max_gun saati geçemez
            model.Add(toplam <= max_gun)

            # Soft MIN: gün aktifse min_gun saat olmalı
            gun_aktif = model.NewBoolVar(f"ga_{tc}_{gun}")
            # Herhangi bir blok bu güne yerleştiyse aktif
            bas_vars = [x[(g["id"], ag, as_)] for g in glist
                        for (ag, as_) in gid_adaylar[g["id"]] if ag == gun]
            if bas_vars:
                model.Add(sum(bas_vars) >= 1).OnlyEnforceIf(gun_aktif)
                model.Add(sum(bas_vars) == 0).OnlyEnforceIf(gun_aktif.Not())

                if min_gun >= 2:
                    # Aktifse toplam saat >= min_gun (soft)
                    eksik = model.NewIntVar(0, min_gun, f"mek_{tc}_{gun}")
                    model.Add(toplam + eksik >= min_gun).OnlyEnforceIf(gun_aktif)
                    model.Add(eksik == 0).OnlyEnforceIf(gun_aktif.Not())
                    cezalar.append(500 * eksik)  # Çok ağır ceza

            # Soft PENCERE: bu günde ilk ve son ders arası boşluk minimize
            # Her saatte bu öğretmen var mı?
            saat_dolu = {}
            for saat in range(1, gun_bilgi[gun] + 1):
                sv = model.NewBoolVar(f"sd_{tc}_{gun}_{saat}")
                aktif_vars = [x[(g["id"], ag, as_)] for g in glist
                              for (ag, as_) in gid_adaylar[g["id"]]
                              if ag == gun and as_ <= saat < as_ + g["boy"]]
                if aktif_vars:
                    model.Add(sum(aktif_vars) >= 1).OnlyEnforceIf(sv)
                    model.Add(sum(aktif_vars) == 0).OnlyEnforceIf(sv.Not())
                else:
                    model.Add(sv == 0)
                saat_dolu[saat] = sv

            # Pencere = boş saatler (ilk ve son dolu saat arasında)
            # Proxy: ardışık olmayan dolu saatler arasındaki boşlukları say
            for saat in range(2, gun_bilgi[gun]):
                # Eğer saat-1 dolu, saat boş, saat+1 dolu = 1 pencere
                pencere_var = model.NewBoolVar(f"pv_{tc}_{gun}_{saat}")
                model.AddBoolAnd([saat_dolu[saat-1], saat_dolu[saat].Not(), saat_dolu[saat+1]]).OnlyEnforceIf(pencere_var)
                model.AddBoolOr([saat_dolu[saat-1].Not(), saat_dolu[saat], saat_dolu[saat+1].Not()]).OnlyEnforceIf(pencere_var.Not())
                cezalar.append(150 * pencere_var)  # Her pencere 150 ceza

    # Amaç: cezaları minimize et
    if cezalar:
        model.Minimize(sum(cezalar))

    # ── ÇÖZE ─────────────────────────────────────────────────────
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 280.0
    solver.parameters.num_workers = 8
    solver.parameters.random_seed = seed
    solver.parameters.log_search_progress = False
    durum = solver.Solve(model)
    sure = time.time() - t0

    print(f"CP-SAT: {solver.StatusName(durum)} {round(sure,1)}s obj={solver.ObjectiveValue() if durum in (cp_model.OPTIMAL,cp_model.FEASIBLE) else 'N/A'}", flush=True)

    basarili = durum in (cp_model.OPTIMAL, cp_model.FEASIBLE)

    # Sonucu slots'a yaz
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
                            "ogretmenler": g["ogrtler"], "kilitli": False
                        }
                    yerlesik = True; break
            if not yerlesik:
                eksikler.append({"sinif": siniflar[g["sid"]].get("sinif_adi"),
                                 "ders": dersler[g["did"]].get("ders_adi"), "blok": g["boy"]})

    print(f"Tamamlandi {round(time.time()-t0,1)}s eksik={len(eksikler)}", flush=True)
    return {"basari": basarili, "slots": slots, "eksikler": eksikler,
            "sure_sn": round(time.time()-t0, 2), "durum": solver.StatusName(durum), "seed": seed}


if __name__ == "__main__":
    test = {
        "siniflar": [{"id": str(i), "sinif_adi": f"9-{i}"} for i in range(1, 4)],
        "dersler": [
            {"id":"101","ders_adi":"Mat","haftalik_saat":4,"blok_dagilim":[2,2],"renk":"#2563eb","kisa_ad":"MAT"},
            {"id":"102","ders_adi":"Tur","haftalik_saat":5,"blok_dagilim":[2,2,1],"renk":"#dc2626","kisa_ad":"TUR"},
        ],
        "atamalar": {str(i): [
            {"ders_id":"101","ogretmen_tc":"TC001","ogretmenler":[{"tc":"TC001"}]},
            {"ders_id":"102","ogretmen_tc":"TC002","ogretmenler":[{"tc":"TC002"}]},
        ] for i in range(1, 4)},
        "kisitlar": {
            "TC001": {"minGunlukSaat": 2, "maxGunlukSaat": 6},
            "TC002": {"minGunlukSaat": 2, "maxGunlukSaat": 6},
        },
        "gunler": [{"gun": i, "saat": 8} for i in range(1, 6)],
        "kilitli": {}
    }
    r = dagit(test)
    print(f"Durum:{r['durum']} Sure:{r['sure_sn']}s Eksik:{len(r['eksikler'])}")
