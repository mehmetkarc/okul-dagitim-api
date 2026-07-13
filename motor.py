"""
OkulYonetimSistemi - Ders Dagitim Motoru STABLE
CP-SAT sadece hard kisitlar. Onceki calisir versiyon.
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

    for g in gorevler:
        av = [x[(g["id"], gun, saat)] for (gun, saat) in gid_adaylar[g["id"]]]
        if av: model.AddExactlyOne(av)

    sid_did = {}
    for g in gorevler: sid_did.setdefault((g["sid"], g["did"]), []).append(g)
    for (sid, did), glist in sid_did.items():
        if len(glist) < 2: continue
        for gun in gunler:
            gv = [x[(g["id"], ag, as_)] for g in glist
                  for (ag, as_) in gid_adaylar[g["id"]] if ag == gun]
            if gv: model.Add(sum(gv) <= 1)

    sid_g = {}
    for g in gorevler: sid_g.setdefault(g["sid"], []).append(g)
    for sid, glist in sid_g.items():
        for gun in gunler:
            for saat in range(1, gun_bilgi[gun] + 1):
                av = [x[(g["id"], ag, as_)] for g in glist
                      for (ag, as_) in gid_adaylar[g["id"]]
                      if ag == gun and as_ <= saat < as_ + g["boy"]]
                if len(av) > 1: model.Add(sum(av) <= 1)

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

    print(f"Tamamlandi {round(time.time()-t0,1)}s eksik={len(eksikler)}", flush=True)
    return {"basari": basarili, "slots": slots, "eksikler": eksikler,
            "sure_sn": round(time.time()-t0, 2), "durum": solver.StatusName(durum), "seed": seed}
