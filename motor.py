"""
OkulYonetimSistemi - Ders Dagitim Motoru v10
Sadece SA - CP-SAT yok, model kurma overhead yok.
Hızlı başlar, 5 dakika optimize eder.
"""
import time, random, sys
from ortools.sat.python import cp_model


class Lookup:
    def __init__(self, gun_bilgi):
        self.sinif_saat = {}
        self.ogrt_saat = {}
        self.did_gun = {}
        self.gun_bilgi = gun_bilgi

    def ekle(self, g, gun, saat):
        for b in range(g["boy"]):
            self.sinif_saat[(g["sid"], gun, saat+b)] = g["id"]
            if g["tc"]: self.ogrt_saat[(g["tc"], gun, saat+b)] = g["id"]
        self.did_gun[(g["sid"], g["did"], gun)] = True

    def kaldir(self, g, gun, saat):
        for b in range(g["boy"]):
            self.sinif_saat.pop((g["sid"], gun, saat+b), None)
            if g["tc"]: self.ogrt_saat.pop((g["tc"], gun, saat+b), None)
        self.did_gun.pop((g["sid"], g["did"], gun), None)

    def ok(self, g, gun, saat, kisitlar):
        k = kisitlar.get(g["tc"], {})
        if g["tc"] and k.get("bosGun") and int(k["bosGun"]) == gun: return False
        if g["tc"] and gun in [int(v) for v in k.get("kapaliGunler", [])]: return False
        ms = self.gun_bilgi.get(gun, 8)
        if saat < 1 or saat + g["boy"] - 1 > ms: return False
        if self.did_gun.get((g["sid"], g["did"], gun)): return False
        for b in range(g["boy"]):
            if (g["sid"], gun, saat+b) in self.sinif_saat: return False
            if g["tc"] and (g["tc"], gun, saat+b) in self.ogrt_saat: return False
        return True


def ceza_tc(tc, konum, glist, kisitlar):
    k = kisitlar.get(tc, {})
    min_g = int(k.get("minGunlukSaat", 2))
    gs = {}
    for g in glist:
        if g["id"] not in konum: continue
        gun, saat = konum[g["id"]]
        if gun not in gs: gs[gun] = set()
        for b in range(g["boy"]): gs[gun].add(saat+b)
    ceza = 0
    hpen = 0
    for gun, s in gs.items():
        n = len(s)
        if n < min_g: ceza += 1000 * (min_g - n)
        if n >= 2:
            sr = sorted(s)
            hpen += sum(1 for i in range(sr[0], sr[-1]) if i not in s)
    if hpen > 2: ceza += 300 * (hpen - 2)
    return ceza


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

    # ── ASAMA 1: CP-SAT sadece hard kisitlarla (20s limit) ──────
    print("CP-SAT basliyor...", flush=True)
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
    solver.parameters.max_time_in_seconds = 20.0  # Kısa - sadece feasible bul
    solver.parameters.num_workers = 8
    solver.parameters.random_seed = seed
    durum = solver.Solve(model)
    t1 = time.time()
    print(f"CP-SAT:{solver.StatusName(durum)} {round(t1-t0,1)}s", flush=True)

    if durum not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"basari": False, "slots": {sid: {} for sid in siniflar},
                "eksikler": [{"sinif": "?", "ders": "Cozum bulunamadi", "blok": 0}],
                "sure_sn": round(t1-t0, 2), "durum": solver.StatusName(durum)}

    # Başlangıç konumunu al
    konum = {}
    lkp = Lookup(gun_bilgi)
    for g in gorevler:
        for (gun, saat) in gid_adaylar[g["id"]]:
            if solver.Value(x[(g["id"], gun, saat)]) == 1:
                konum[g["id"]] = (gun, saat)
                lkp.ekle(g, gun, saat)
                break

    # ── ASAMA 2: SA ile min/max + pencere optimizasyonu ─────────
    kalan = 310 - (time.time() - t0)
    print(f"SA: {round(kalan)}s", flush=True)

    tc_list = list(tc_g.keys())
    tc_ceza = {tc: ceza_tc(tc, konum, tc_g[tc], kisitlar) for tc in tc_list}
    toplam = sum(tc_ceza.values())
    en_iyi = toplam
    en_iyi_konum = dict(konum)

    print(f"SA baslangic ceza:{toplam}", flush=True)

    sicaklik = 2000.0
    soguma = 0.99999
    iterasyon = 0
    son_log = time.time()
    t_sa = time.time()

    while time.time() - t_sa < kalan:
        iterasyon += 1
        if time.time() - son_log > 20:
            print(f"SA iter={iterasyon} ceza={toplam} T={sicaklik:.1f}", flush=True)
            son_log = time.time()
        if toplam == 0: break

        # Önce cezalıya odaklan
        cezali = [(tc, c) for tc, c in tc_ceza.items() if c > 0]
        if random.random() < 0.8 and cezali:
            total_c = sum(c for _, c in cezali)
            r = random.random() * total_c
            cum = 0; tc = cezali[0][0]
            for t_, c in cezali:
                cum += c
                if r <= cum: tc = t_; break
        else:
            tc = random.choice(tc_list)

        glist = tc_g[tc]
        g = random.choice(glist)
        if g["id"] not in konum: continue
        eg, es = konum[g["id"]]
        adaylar = gid_adaylar[g["id"]]
        if len(adaylar) < 2: continue
        yg, ys = random.choice(adaylar)
        if (yg, ys) == (eg, es): continue

        lkp.kaldir(g, eg, es)
        if not lkp.ok(g, yg, ys, kisitlar):
            lkp.ekle(g, eg, es); continue

        eski_c = tc_ceza[tc]
        konum[g["id"]] = (yg, ys)
        lkp.ekle(g, yg, ys)
        yeni_c = ceza_tc(tc, konum, glist, kisitlar)
        delta = yeni_c - eski_c

        if delta <= 0 or (sicaklik > 0.01 and random.random() < pow(2.718, -delta/sicaklik)):
            tc_ceza[tc] = yeni_c
            toplam += delta
            if toplam < en_iyi:
                en_iyi = toplam
                en_iyi_konum = dict(konum)
        else:
            konum[g["id"]] = (eg, es)
            lkp.kaldir(g, yg, ys)
            lkp.ekle(g, eg, es)

        sicaklik *= soguma

    print(f"SA bitti:{iterasyon} iter en_iyi={en_iyi}", flush=True)
    konum = en_iyi_konum

    # Sonucu yaz
    slots = {sid: {} for sid in siniflar}
    eksikler = []
    for g in gorevler:
        if g["id"] not in konum:
            eksikler.append({"sinif": siniflar[g["sid"]].get("sinif_adi"),
                             "ders": dersler[g["did"]].get("ders_adi"), "blok": g["boy"]}); continue
        gun, saat = konum[g["id"]]
        sid = g["sid"]; ders = dersler[g["did"]]
        if gun not in slots[sid]: slots[sid][gun] = {}
        for b in range(g["boy"]):
            slots[sid][gun][saat+b] = {"ders_id": g["did"], "ders_adi": ders.get("ders_adi",""),
                "kisa_ad": ders.get("kisa_ad", ders.get("ders_adi","")[:4]),
                "renk": ders.get("renk","#1a6b47"), "ogretmen_tc": g["tc"],
                "ogretmenler": g["ogrtler"], "kilitli": False}

    sure = time.time()-t0
    print(f"Tamamlandi {round(sure,1)}s eksik={len(eksikler)}", flush=True)
    return {"basari": True, "slots": slots, "eksikler": eksikler,
            "sure_sn": round(sure,2), "durum": solver.StatusName(durum), "seed": seed}


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
