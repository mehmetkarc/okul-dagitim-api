"""
OkulYonetimSistemi - Ders Dagitim Motoru v7
CP-SAT + Simulated Annealing (hızlı lookup ile)
minGunlukSaat=2, maxGunlukSaat=6/8, pencere<=2
"""
import time, random, sys
from ortools.sat.python import cp_model


def asama1_cpsat(gorevler, gid_adaylar, gun_bilgi, gunler, seed, max_sure=120):
    model = cp_model.CpModel()
    x = {}
    for g in gorevler:
        for (gun, saat) in gid_adaylar[g["id"]]:
            x[(g["id"], gun, saat)] = model.NewBoolVar(f"x_{g['id']}_{gun}_{saat}")

    for g in gorevler:
        av = [x[(g["id"], gun, saat)] for (gun, saat) in gid_adaylar[g["id"]]]
        if av: model.AddExactlyOne(av)

    sid_did = {}
    for g in gorevler: sid_did.setdefault((g["sid"], g["did"]), []).append(g)
    for (sid, did), glist in sid_did.items():
        if len(glist) < 2: continue
        for gun in gunler:
            gv = [x[(g["id"], ag, as_)] for g in glist for (ag, as_) in gid_adaylar[g["id"]] if ag == gun]
            if gv: model.Add(sum(gv) <= 1)

    sid_g = {}
    for g in gorevler: sid_g.setdefault(g["sid"], []).append(g)
    for sid, glist in sid_g.items():
        for gun in gunler:
            for saat in range(1, gun_bilgi[gun] + 1):
                av = [x[(g["id"], ag, as_)] for g in glist for (ag, as_) in gid_adaylar[g["id"]]
                      if ag == gun and as_ <= saat < as_ + g["boy"]]
                if len(av) > 1: model.Add(sum(av) <= 1)

    tc_g = {}
    for g in gorevler:
        if g["tc"]: tc_g.setdefault(g["tc"], []).append(g)
    for tc, glist in tc_g.items():
        for gun in gunler:
            for saat in range(1, gun_bilgi[gun] + 1):
                av = [x[(g["id"], ag, as_)] for g in glist for (ag, as_) in gid_adaylar[g["id"]]
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
                    konum[g["id"]] = (gun, saat); break
    return konum, solver.StatusName(durum)


class HizliLookup:
    """O(1) çakışma kontrolü için lookup tabloları."""
    def __init__(self, gorevler, konum, gun_bilgi):
        self.sinif_saat = {}   # (sid, gun, saat) -> gid
        self.ogrt_saat = {}    # (tc, gun, saat)  -> gid
        self.did_gun = {}      # (sid, did, gun)   -> True
        self.gun_bilgi = gun_bilgi
        
        for g in gorevler:
            gid = g["id"]
            if gid not in konum: continue
            gun, saat = konum[gid]
            self._ekle(g, gun, saat)
    
    def _ekle(self, g, gun, saat):
        gid = g["id"]
        for b in range(g["boy"]):
            self.sinif_saat[(g["sid"], gun, saat+b)] = gid
            if g["tc"]:
                self.ogrt_saat[(g["tc"], gun, saat+b)] = gid
        self.did_gun[(g["sid"], g["did"], gun)] = True
    
    def _kaldir(self, g, gun, saat):
        for b in range(g["boy"]):
            self.sinif_saat.pop((g["sid"], gun, saat+b), None)
            if g["tc"]:
                self.ogrt_saat.pop((g["tc"], gun, saat+b), None)
        self.did_gun.pop((g["sid"], g["did"], gun), None)
    
    def cakisma_var_mi(self, g, yeni_gun, yeni_saat, kisitlar, gid_adaylar):
        k = kisitlar.get(g["tc"], {})
        if g["tc"] and k.get("bosGun") and int(k["bosGun"]) == yeni_gun: return True
        if g["tc"] and yeni_gun in [int(v) for v in k.get("kapaliGunler", [])]: return True
        max_s = self.gun_bilgi.get(yeni_gun, 8)
        if yeni_saat + g["boy"] - 1 > max_s: return True
        if self.did_gun.get((g["sid"], g["did"], yeni_gun)): return True
        for b in range(g["boy"]):
            if (g["sid"], yeni_gun, yeni_saat+b) in self.sinif_saat: return True
            if g["tc"] and (g["tc"], yeni_gun, yeni_saat+b) in self.ogrt_saat: return True
        return False
    
    def tasima_yap(self, g, eski_gun, eski_saat, yeni_gun, yeni_saat):
        self._kaldir(g, eski_gun, eski_saat)
        self._ekle(g, yeni_gun, yeni_saat)


def _pencere(saatler):
    if len(saatler) < 2: return 0
    s = sorted(saatler)
    return sum(1 for i in range(s[0], s[-1]) if i not in saatler)


def ceza_tc(tc, konum, gorevler_tc, kisitlar):
    k = kisitlar.get(tc, {})
    min_g = int(k.get("minGunlukSaat", 2))
    max_g = int(k.get("maxGunlukSaat", 8))
    gun_saatler = {}
    for g in gorevler_tc:
        if g["id"] not in konum: continue
        gun, saat = konum[g["id"]]
        if gun not in gun_saatler: gun_saatler[gun] = set()
        for b in range(g["boy"]): gun_saatler[gun].add(saat+b)
    
    ceza = 0
    haf_pencere = 0
    for gun, saatler in gun_saatler.items():
        n = len(saatler)
        if n < min_g: ceza += 1000 * (min_g - n)  # Günde tek ders — çok ağır
        if n > max_g: ceza += 500 * (n - max_g)
        p = _pencere(saatler)
        haf_pencere += p
    if haf_pencere > 2: ceza += 300 * (haf_pencere - 2)
    return ceza


def simulated_annealing(konum, gorevler, gid_adaylar, kisitlar, gun_bilgi, max_sure=160):
    t0 = time.time()
    konum = dict(konum)
    
    tc_g = {}
    for g in gorevler:
        if g["tc"]: tc_g.setdefault(g["tc"], []).append(g)
    
    lookup = HizliLookup(gorevler, konum, gun_bilgi)
    
    # Başlangıç cezası
    tc_ceza = {tc: ceza_tc(tc, konum, glist, kisitlar) for tc, glist in tc_g.items()}
    toplam = sum(tc_ceza.values())
    en_iyi = toplam
    en_iyi_konum = dict(konum)
    
    print(f"  SA baslangic ceza: {toplam}", flush=True)
    
    sicaklik = 300.0
    soguma = 0.9998
    iter_sayisi = 0
    son_log = t0
    
    tc_list = list(tc_g.keys())
    
    while time.time() - t0 < max_sure:
        iter_sayisi += 1
        
        # Her 10s'de log
        if time.time() - son_log > 10:
            print(f"  SA iter={iter_sayisi} ceza={toplam} sicaklik={sicaklik:.1f}", flush=True)
            son_log = time.time()
        
        # Cezalı öğretmene odaklan (%80) veya rastgele (%20)
        if random.random() < 0.8:
            cezali = [(tc, c) for tc, c in tc_ceza.items() if c > 0]
            if not cezali:
                if toplam == 0: break
                tc = random.choice(tc_list)
            else:
                total_c = sum(c for _, c in cezali)
                r = random.random() * total_c
                cum = 0; tc = cezali[0][0]
                for t, c in cezali:
                    cum += c
                    if r <= cum: tc = t; break
        else:
            tc = random.choice(tc_list)
        
        glist = tc_g[tc]
        g = random.choice(glist)
        gid = g["id"]
        if gid not in konum: continue
        
        adaylar = gid_adaylar[gid]
        if len(adaylar) < 2: continue
        yeni = random.choice(adaylar)
        eski_gun, eski_saat = konum[gid]
        yeni_gun, yeni_saat = yeni
        if (yeni_gun, yeni_saat) == (eski_gun, eski_saat): continue
        
        # Geçici kaldır, çakışma kontrol
        lookup._kaldir(g, eski_gun, eski_saat)
        cakisma = lookup.cakisma_var_mi(g, yeni_gun, yeni_saat, kisitlar, gid_adaylar)
        
        if cakisma:
            lookup._ekle(g, eski_gun, eski_saat)
            continue
        
        # Ceza farkı
        eski_ceza_tc = tc_ceza[tc]
        konum[gid] = (yeni_gun, yeni_saat)
        lookup._ekle(g, yeni_gun, yeni_saat)
        yeni_ceza_tc = ceza_tc(tc, konum, glist, kisitlar)
        delta = yeni_ceza_tc - eski_ceza_tc
        
        if delta <= 0 or (sicaklik > 0.1 and random.random() < pow(2.718, -delta/sicaklik)):
            tc_ceza[tc] = yeni_ceza_tc
            toplam += delta
            if toplam < en_iyi:
                en_iyi = toplam
                en_iyi_konum = dict(konum)
        else:
            # Geri al
            konum[gid] = (eski_gun, eski_saat)
            lookup._kaldir(g, yeni_gun, yeni_saat)
            lookup._ekle(g, eski_gun, eski_saat)
        
        sicaklik *= soguma
    
    print(f"  SA bitti: {iter_sayisi} iter, en_iyi_ceza={en_iyi}, sure={round(time.time()-t0,1)}s", flush=True)
    return en_iyi_konum


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
        return {"basari":True,"slots":{sid:{} for sid in siniflar},"eksikler":[],"sure_sn":0,"durum":"EMPTY"}

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

    print(f"Asama1: {len(gorevler)} gorev, {len(siniflar)} sinif", flush=True)
    konum, cp_durum = asama1_cpsat(gorevler, gid_adaylar, gun_bilgi, gunler, seed, max_sure=100)

    if not konum:
        return {"basari":False,"slots":{sid:{} for sid in siniflar},
                "eksikler":[{"sinif":"?","ders":"CP-SAT bulamadi","blok":0}],
                "sure_sn":round(time.time()-t0,2),"durum":cp_durum}

    print(f"CP-SAT: {cp_durum} {round(time.time()-t0,1)}s", flush=True)

    kalan = 280 - (time.time() - t0)
    print(f"SA: {round(kalan)}s sure", flush=True)
    konum = simulated_annealing(konum, gorevler, gid_adaylar, kisitlar, gun_bilgi, max_sure=kalan)

    slots = {sid:{} for sid in siniflar}
    eksikler = []
    for g in gorevler:
        if g["id"] not in konum:
            eksikler.append({"sinif":siniflar[g["sid"]].get("sinif_adi"),
                             "ders":dersler[g["did"]].get("ders_adi"),"blok":g["boy"]}); continue
        gun, saat = konum[g["id"]]
        sid = g["sid"]; ders = dersler[g["did"]]
        if gun not in slots[sid]: slots[sid][gun] = {}
        for b in range(g["boy"]):
            slots[sid][gun][saat+b] = {"ders_id":g["did"],"ders_adi":ders.get("ders_adi",""),
                "kisa_ad":ders.get("kisa_ad",ders.get("ders_adi","")[:4]),"renk":ders.get("renk","#1a6b47"),
                "ogretmen_tc":g["tc"],"ogretmenler":g["ogrtler"],"kilitli":False}

    print(f"Tamamlandi {round(time.time()-t0,1)}s eksik={len(eksikler)}", flush=True)
    return {"basari":True,"slots":slots,"eksikler":eksikler,
            "sure_sn":round(time.time()-t0,2),"durum":cp_durum,"seed":seed}


if __name__ == "__main__":
    test = {"siniflar":[{"id":str(i),"sinif_adi":f"9-{chr(64+i)}"} for i in range(1,6)],
            "dersler":[{"id":"101","ders_adi":"Mat","haftalik_saat":4,"blok_dagilim":[2,2],"renk":"#2563eb","kisa_ad":"MAT"},
                       {"id":"102","ders_adi":"Tur","haftalik_saat":5,"blok_dagilim":[2,2,1],"renk":"#dc2626","kisa_ad":"TUR"}],
            "atamalar":{str(i):[{"ders_id":"101","ogretmen_tc":"TC001","ogretmenler":[{"tc":"TC001"}]},
                                 {"ders_id":"102","ogretmen_tc":"TC002","ogretmenler":[{"tc":"TC002"}]}] for i in range(1,6)},
            "kisitlar":{"TC001":{"minGunlukSaat":2,"maxGunlukSaat":6},"TC002":{"minGunlukSaat":2,"maxGunlukSaat":8}},
            "gunler":[{"gun":i,"saat":8} for i in range(1,6)],"kilitli":{}}
    r = dagit(test)
    print(f"Durum:{r['durum']} Sure:{r['sure_sn']}s Eksik:{len(r['eksikler'])}")
