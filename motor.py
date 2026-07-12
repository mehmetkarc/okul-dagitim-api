"""
OkulYonetimSistemi - Ders Dagitim Motoru v4
OR-Tools CP-SAT.
Kısıtlar:
- Blok butunlugu, ogretmen/sinif cakismasi, blok-gun kurali
- Min 2 / Max 8 gunluk ders (hard)
- Pencere sayisi haftada max 2 (soft, agir ceza)
- Bos gun atama
- Her dağıtımda farklı seed
"""
import time, random
from ortools.sat.python import cp_model

def dagit(veri):
    t0 = time.time()
    model = cp_model.CpModel()
    siniflar   = {str(s["id"]): s for s in veri.get("siniflar", [])}
    dersler    = {str(d["id"]): d for d in veri.get("dersler", [])}
    atamalar   = {str(k): v for k, v in veri.get("atamalar", {}).items()}
    kisitlar   = {str(k): v for k, v in veri.get("kisitlar", {}).items()}
    gun_bilgi  = {int(g["gun"]): int(g["saat"]) for g in veri.get("gunler", [])}
    kilitli    = veri.get("kilitli", {})
    gunler     = sorted(gun_bilgi.keys())
    seed       = veri.get("seed", random.randint(1, 999999))
    MAX_SAAT   = max(gun_bilgi.values(), default=8)

    # ── Görev listesi ────────────────────────────────────────────
    gorevler = []
    for sid, atama_list in atamalar.items():
        if sid not in siniflar: continue
        for atama in atama_list:
            did = str(atama.get("ders_id",""))
            if did not in dersler: continue
            ders = dersler[did]
            tc = str(atama.get("ogretmen_tc") or
                     (atama.get("ogretmenler") or [{}])[0].get("tc") or "")
            bloklar = ders.get("blok_dagilim") or [ders.get("haftalik_saat",1)]
            for bi, boy in enumerate(bloklar):
                if not boy: continue
                gorevler.append({
                    "id": f"{sid}_{did}_{bi}",
                    "sid": sid, "did": did, "tc": tc,
                    "ogrtler": atama.get("ogretmenler",[]),
                    "boy": int(boy), "bi": bi
                })

    if not gorevler:
        return {"basari":True,"slots":{sid:{} for sid in siniflar},
                "eksikler":[],"sure_sn":0,"durum":"EMPTY"}

    # ── Karar değişkenleri ───────────────────────────────────────
    x = {}
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
                key = (gid, gun, saat)
                x[key] = model.NewBoolVar(f"x_{gid}_{gun}_{saat}")
                gid_adaylar[gid].append((gun, saat))

    # ── Hard: Her görev tam 1 yere ───────────────────────────────
    for g in gorevler:
        av = gid_adaylar[g["id"]]
        if av:
            model.AddExactlyOne([x[(g["id"],gun,saat)] for (gun,saat) in av])

    # ── Hard: Blok-gün kuralı ─────────────────────────────────────
    sid_did = {}
    for g in gorevler:
        sid_did.setdefault((g["sid"],g["did"]),[]).append(g)
    for (sid,did), glist in sid_did.items():
        if len(glist) < 2: continue
        for gun in gunler:
            gv = [x[(g["id"],ag,as_)]
                  for g in glist
                  for (ag,as_) in gid_adaylar[g["id"]] if ag==gun]
            if gv: model.Add(sum(gv) <= 1)

    # ── Hard: Sınıf çakışması ────────────────────────────────────
    sid_g = {}
    for g in gorevler: sid_g.setdefault(g["sid"],[]).append(g)
    for sid, glist in sid_g.items():
        for gun in gunler:
            for saat in range(1, gun_bilgi[gun]+1):
                av = [x[(g["id"],ag,as_)]
                      for g in glist
                      for (ag,as_) in gid_adaylar[g["id"]]
                      if ag==gun and as_<=saat<as_+g["boy"]]
                if len(av) > 1: model.Add(sum(av) <= 1)

    # ── Hard: Öğretmen çakışması ─────────────────────────────────
    tc_g = {}
    for g in gorevler:
        if g["tc"]: tc_g.setdefault(g["tc"],[]).append(g)
    for tc, glist in tc_g.items():
        for gun in gunler:
            for saat in range(1, gun_bilgi[gun]+1):
                av = [x[(g["id"],ag,as_)]
                      for g in glist
                      for (ag,as_) in gid_adaylar[g["id"]]
                      if ag==gun and as_<=saat<as_+g["boy"]]
                if len(av) > 1: model.Add(sum(av) <= 1)

    # ── Öğretmen bazlı kısıtlar ───────────────────────────────────
    cezalar = []

    for tc, glist in tc_g.items():
        k = kisitlar.get(tc, {})
        min_gun = int(k.get("minGunlukSaat", 2))
        max_gun = int(k.get("maxGunlukSaat", 8))

        for gun in gunler:
            # Bu günde öğretmenin toplam saati
            # Dikkat: bir blok başlangıcı o bloğun tüm saatlerini kapsar
            # Sadece başlangıç değişkenlerini sayıyoruz × boy
            bas_vars = []  # (var, boy) çiftleri
            for g in glist:
                gid = g["id"]
                for (ag, as_) in gid_adaylar[gid]:
                    if ag == gun:
                        bas_vars.append((x[(gid,ag,as_)], g["boy"]))

            if not bas_vars:
                continue

            # Toplam saat = sum(var * boy)
            toplam_saat = sum(v * b for v, b in bas_vars)

            # ── HARD MAX: günde en fazla max_gun saat ────────────
            model.Add(toplam_saat <= max_gun)

            # ── HARD MIN: gün aktifse en az min_gun saat ─────────
            # "gün aktif" = en az 1 blok o güne yerleşti
            gun_aktif = model.NewBoolVar(f"ga_{tc}_{gun}")
            blok_sayisi = sum(v for v, b in bas_vars)
            model.Add(blok_sayisi >= 1).OnlyEnforceIf(gun_aktif)
            model.Add(blok_sayisi == 0).OnlyEnforceIf(gun_aktif.Not())

            # Gün aktifse toplam saat >= min_gun (soft — ceza ile)
            if min_gun >= 2:
                eksik = model.NewIntVar(0, min_gun, f"min_eks_{tc}_{gun}")
                model.Add(toplam_saat + eksik >= min_gun).OnlyEnforceIf(gun_aktif)
                model.Add(eksik == 0).OnlyEnforceIf(gun_aktif.Not())
                cezalar.append(200 * eksik)  # Ağır ceza — günde tek ders çok istenmiyor

        # ── SOFT: Pencere sayısı haftada max 2 ───────────────────
        # Pencere = bir günde ilk ve son ders arasındaki boş saatler
        # Her saat için: öğretmen o saatte mi? -> binary
        # Pencere = dolu saatler arasındaki boş saatler
        # Proxy: her gün için (son_saat - ilk_saat + 1 - toplam_saat) = o günün penceresi
        # Bunu doğrudan modellemek zor; bunun yerine "gün içi boşluk" minimize et

        for gun in gunler:
            k2 = kisitlar.get(tc, {})
            if k2.get("bosGun") and int(k2["bosGun"]) == gun: continue

            # Bu günde her saatte öğretmen var mı?
            saat_var = {}
            for saat in range(1, gun_bilgi[gun]+1):
                sv = model.NewBoolVar(f"sv_{tc}_{gun}_{saat}")
                # sv = 1 iff öğretmen bu saatte bu günde ders veriyor
                aktif_vars = [x[(g["id"],ag,as_)]
                              for g in glist
                              for (ag,as_) in gid_adaylar[g["id"]]
                              if ag==gun and as_<=saat<as_+g["boy"]]
                if aktif_vars:
                    model.Add(sum(aktif_vars) >= 1).OnlyEnforceIf(sv)
                    model.Add(sum(aktif_vars) == 0).OnlyEnforceIf(sv.Not())
                else:
                    model.Add(sv == 0)
                saat_var[saat] = sv

            # İlk ve son saat (linearize)
            max_s = gun_bilgi[gun]
            ilk_saat  = model.NewIntVar(0, max_s+1, f"ilk_{tc}_{gun}")
            son_saat  = model.NewIntVar(0, max_s,   f"son_{tc}_{gun}")
            gun_dolu  = model.NewBoolVar(f"gd_{tc}_{gun}")

            # gun_dolu: o gün en az 1 ders var
            all_sv = [saat_var[s] for s in range(1, max_s+1)]
            model.Add(sum(all_sv) >= 1).OnlyEnforceIf(gun_dolu)
            model.Add(sum(all_sv) == 0).OnlyEnforceIf(gun_dolu.Not())

            # ilk_saat ve son_saat — her saat için yardımcı constraint
            for saat in range(1, max_s+1):
                sv = saat_var[saat]
                # ilk_saat <= saat eğer sv=1
                b_ilk = model.NewBoolVar(f"bi_{tc}_{gun}_{saat}")
                model.Add(ilk_saat <= saat).OnlyEnforceIf(b_ilk)
                # son_saat >= saat eğer sv=1
                model.Add(son_saat >= saat).OnlyEnforceIf(sv)

            # Pencere = son_saat - ilk_saat + 1 - toplam_dolu_saat
            toplam_dolu = sum(saat_var[s] for s in range(1, max_s+1))
            pencere = model.NewIntVar(0, max_s, f"pen_{tc}_{gun}")
            model.Add(pencere == son_saat - ilk_saat + 1 - toplam_dolu).OnlyEnforceIf(gun_dolu)
            model.Add(pencere == 0).OnlyEnforceIf(gun_dolu.Not())

            # Haftalık toplam pencere <= 2 (soft, ağır ceza)
            pen_asim = model.NewIntVar(0, max_s, f"pen_asim_{tc}_{gun}")
            model.Add(pencere <= 2 + pen_asim)
            cezalar.append(150 * pen_asim)

    # ── Amaç: cezaları minimize et ───────────────────────────────
    if cezalar:
        model.Minimize(sum(cezalar))

    # ── Çöz ──────────────────────────────────────────────────────
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 55.0
    solver.parameters.num_workers = 4
    solver.parameters.random_seed = seed
    solver.parameters.log_search_progress = False
    durum = solver.Solve(model)
    sure  = time.time() - t0
    basarili = durum in (cp_model.OPTIMAL, cp_model.FEASIBLE)

    # ── Sonucu slots formatına çevir ─────────────────────────────
    slots = {sid: {} for sid in siniflar}
    eksikler = []
    if basarili:
        for g in gorevler:
            gid = g["id"]
            yerlesik = False
            for (gun, saat) in gid_adaylar[gid]:
                if solver.Value(x[(gid,gun,saat)]) == 1:
                    sid  = g["sid"]
                    ders = dersler[g["did"]]
                    if gun not in slots[sid]: slots[sid][gun] = {}
                    for b in range(g["boy"]):
                        slots[sid][gun][saat+b] = {
                            "ders_id": g["did"],
                            "ders_adi": ders.get("ders_adi",""),
                            "kisa_ad": ders.get("kisa_ad", ders.get("ders_adi","")[:4]),
                            "renk": ders.get("renk","#1a6b47"),
                            "ogretmen_tc": g["tc"],
                            "ogretmenler": g["ogrtler"],
                            "kilitli": False
                        }
                    yerlesik = True
                    break
            if not yerlesik:
                eksikler.append({
                    "sinif": siniflar[g["sid"]].get("sinif_adi"),
                    "ders":  dersler[g["did"]].get("ders_adi"),
                    "blok":  g["boy"]
                })

    return {
        "basari":   basarili,
        "slots":    slots,
        "eksikler": eksikler,
        "sure_sn":  round(sure, 2),
        "durum":    solver.StatusName(durum),
        "seed":     seed
    }


if __name__ == "__main__":
    test = {
        "siniflar": [{"id": str(i), "sinif_adi": f"9-{chr(64+i)}"} for i in range(1,4)],
        "dersler": [
            {"id":"101","ders_adi":"Matematik","haftalik_saat":4,"blok_dagilim":[2,2],"renk":"#2563eb","kisa_ad":"MAT"},
            {"id":"102","ders_adi":"Turkce","haftalik_saat":5,"blok_dagilim":[2,2,1],"renk":"#dc2626","kisa_ad":"TUR"},
            {"id":"103","ders_adi":"Beden","haftalik_saat":2,"blok_dagilim":[2],"renk":"#16a34a","kisa_ad":"BED"},
        ],
        "atamalar": {str(i): [
            {"ders_id":"101","ogretmen_tc":"TC001","ogretmenler":[{"tc":"TC001"}]},
            {"ders_id":"102","ogretmen_tc":"TC002","ogretmenler":[{"tc":"TC002"}]},
            {"ders_id":"103","ogretmen_tc":"TC003","ogretmenler":[{"tc":"TC003"}]},
        ] for i in range(1,4)},
        "kisitlar": {
            "TC001": {"minGunlukSaat": 2, "maxGunlukSaat": 6},
            "TC002": {"minGunlukSaat": 2, "maxGunlukSaat": 8},
        },
        "gunler": [{"gun": i, "saat": 8} for i in range(1,6)],
        "kilitli": {}
    }
    r = dagit(test)
    print(f"Durum:{r['durum']} Sure:{r['sure_sn']}s Eksik:{len(r['eksikler'])} Seed:{r['seed']}")
