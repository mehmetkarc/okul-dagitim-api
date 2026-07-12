"""
OkulYonetimSistemi - Ders Dagitim Motoru v5
2 asamali: 
  Asama 1: CP-SAT ile tum dersleri yerlestirir (hard kisitlar)
  Asama 2: Local search ile pencereleri ve min gunluk dersi optimize eder
"""
import time, random, copy
from ortools.sat.python import cp_model


def _asama1_yerlesim(gorevler, gid_adaylar, x, siniflar, dersler, gun_bilgi, gunler, seed, max_sure=45):
    """CP-SAT ile sadece hard kisitlarla tum dersleri yerlestirir."""
    model = cp_model.CpModel()

    # Degiskenleri modele ekle
    for key, bv in x.items():
        # CP-SAT modelde yeniden tanimla
        pass
    
    # Yeniden model kur
    model2 = cp_model.CpModel()
    x2 = {key: model2.NewBoolVar(f"x2_{key[0]}_{key[1]}_{key[2]}") for key in x}

    for g in gorevler:
        av = gid_adaylar[g["id"]]
        if av:
            model2.AddExactlyOne([x2[(g["id"],gun,saat)] for (gun,saat) in av])

    sid_did = {}
    for g in gorevler: sid_did.setdefault((g["sid"],g["did"]),[]).append(g)
    for (sid,did), glist in sid_did.items():
        if len(glist)<2: continue
        for gun in gunler:
            gv=[x2[(g["id"],ag,as_)] for g in glist for (ag,as_) in gid_adaylar[g["id"]] if ag==gun]
            if gv: model2.Add(sum(gv)<=1)

    sid_g = {}
    for g in gorevler: sid_g.setdefault(g["sid"],[]).append(g)
    for sid, glist in sid_g.items():
        for gun in gunler:
            for saat in range(1, gun_bilgi[gun]+1):
                av=[x2[(g["id"],ag,as_)] for g in glist for (ag,as_) in gid_adaylar[g["id"]] if ag==gun and as_<=saat<as_+g["boy"]]
                if len(av)>1: model2.Add(sum(av)<=1)

    tc_g = {}
    for g in gorevler:
        if g["tc"]: tc_g.setdefault(g["tc"],[]).append(g)
    for tc, glist in tc_g.items():
        for gun in gunler:
            for saat in range(1, gun_bilgi[gun]+1):
                av=[x2[(g["id"],ag,as_)] for g in glist for (ag,as_) in gid_adaylar[g["id"]] if ag==gun and as_<=saat<as_+g["boy"]]
                if len(av)>1: model2.Add(sum(av)<=1)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max_sure
    solver.parameters.num_workers = 4
    solver.parameters.random_seed = seed
    durum = solver.Solve(model2)
    
    sonuc = {}
    if durum in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for g in gorevler:
            for (gun,saat) in gid_adaylar[g["id"]]:
                if solver.Value(x2[(g["id"],gun,saat)])==1:
                    sonuc[g["id"]] = (gun, saat)
                    break
    return sonuc, solver.StatusName(durum)


def _ogrt_gun_saatleri(tc, konum, gorevler, gun_bilgi):
    """Bir ogretmenin her gundeki mesgul saatlerini dondurur."""
    gun_saatler = {gun: set() for gun in gun_bilgi}
    for g in gorevler:
        if g["tc"] != tc: continue
        if g["id"] not in konum: continue
        gun, saat = konum[g["id"]]
        for b in range(g["boy"]):
            gun_saatler[gun].add(saat+b)
    return gun_saatler


def _pencere_sayisi(dolu_saatler):
    """Bir gundeki dolu saatler listesinden pencere sayisini hesaplar."""
    if len(dolu_saatler) < 2: return 0
    s = sorted(dolu_saatler)
    pencere = 0
    for i in range(s[0], s[-1]+1):
        if i not in dolu_saatler:
            pencere += 1
    return pencere


def _asama2_optimize(gorevler, gid_adaylar, konum, gun_bilgi, gunler, kisitlar, max_sure=30):
    """
    Local search ile pencere ve min gunluk dersi optimize eder.
    Her iterasyonda bir ogretmenin bir dersini baska konuma tasir,
    eger toplam ceza azaliyorsa kabul eder.
    """
    t0 = time.time()
    
    # Ceza fonksiyonu
    def ceza_hesapla(tc, gun_saatler, k):
        min_gun = int(k.get("minGunlukSaat", 2))
        max_gun = int(k.get("maxGunlukSaat", 8))
        toplam = 0
        for gun, saatler in gun_saatler.items():
            n = len(saatler)
            if n == 0: continue
            # Gunluk min ihlali
            if n < min_gun: toplam += 500 * (min_gun - n)
            # Gunluk max ihlali
            if n > max_gun: toplam += 500 * (n - max_gun)
            # Pencere cezasi
            p = _pencere_sayisi(saatler)
            if p > 2: toplam += 200 * (p - 2)
            elif p > 0: toplam += 50 * p
        return toplam

    # Tum ogretmenlerin mevcut cezasini hesapla
    tc_g = {}
    for g in gorevler:
        if g["tc"]: tc_g.setdefault(g["tc"],[]).append(g)

    konum = dict(konum)  # kopya al
    iterasyon = 0
    MAX_ITER = 50000

    while time.time()-t0 < max_sure and iterasyon < MAX_ITER:
        iterasyon += 1
        # Rastgele bir ogretmen sec
        if not tc_g: break
        tc = random.choice(list(tc_g.keys()))
        glist = tc_g[tc]
        if not glist: continue
        k = kisitlar.get(tc, {})

        # Bu ogretmenin bir dersini rastgele baska yere tasimayı dene
        g = random.choice(glist)
        gid = g["id"]
        adaylar = gid_adaylar[gid]
        if len(adaylar) < 2: continue

        eski_konum = konum.get(gid)
        if not eski_konum: continue

        # Yeni rastgele konum sec
        yeni_aday = random.choice(adaylar)
        if yeni_aday == eski_konum: continue

        yeni_gun, yeni_saat = yeni_aday
        eski_gun, eski_saat = eski_konum

        # Cakisma kontrolu
        cakisma = False
        # Sinif cakismasi
        for g2 in [gg for gg in gorevler if gg["sid"]==g["sid"] and gg["id"]!=gid]:
            if g2["id"] not in konum: continue
            g2_gun, g2_saat = konum[g2["id"]]
            if g2_gun == yeni_gun:
                for b in range(g["boy"]):
                    for b2 in range(g2["boy"]):
                        if yeni_saat+b == g2_saat+b2: cakisma=True; break
                if cakisma: break
        if cakisma: continue

        # Ogretmen cakismasi
        for g2 in [gg for gg in glist if gg["id"]!=gid]:
            if g2["id"] not in konum: continue
            g2_gun, g2_saat = konum[g2["id"]]
            if g2_gun == yeni_gun:
                for b in range(g["boy"]):
                    for b2 in range(g2["boy"]):
                        if yeni_saat+b == g2_saat+b2: cakisma=True; break
                if cakisma: break
        if cakisma: continue

        # Blok-gun kurali
        for g2 in [gg for gg in glist if gg["id"]!=gid and gg["did"]==g["did"] and gg["sid"]==g["sid"]]:
            if g2["id"] not in konum: continue
            if konum[g2["id"]][0] == yeni_gun: cakisma=True; break
        if cakisma: continue

        # Ceza karsilastir
        gs_eski = _ogrt_gun_saatleri(tc, konum, gorevler, gun_bilgi)
        ceza_eski = ceza_hesapla(tc, gs_eski, k)

        konum[gid] = yeni_aday
        gs_yeni = _ogrt_gun_saatleri(tc, konum, gorevler, gun_bilgi)
        ceza_yeni = ceza_hesapla(tc, gs_yeni, k)

        if ceza_yeni <= ceza_eski:
            pass  # Kabul et
        else:
            konum[gid] = eski_konum  # Geri al

    return konum


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

    # Gorev listesi
    gorevler = []
    for sid, atama_list in atamalar.items():
        if sid not in siniflar: continue
        for atama in atama_list:
            did = str(atama.get("ders_id",""))
            if did not in dersler: continue
            ders = dersler[did]
            tc = str(atama.get("ogretmen_tc") or (atama.get("ogretmenler") or [{}])[0].get("tc") or "")
            bloklar = ders.get("blok_dagilim") or [ders.get("haftalik_saat",1)]
            for bi, boy in enumerate(bloklar):
                if not boy: continue
                gorevler.append({"id":f"{sid}_{did}_{bi}","sid":sid,"did":did,"tc":tc,
                                 "ogrtler":atama.get("ogretmenler",[]),"boy":int(boy),"bi":bi})

    if not gorevler:
        return {"basari":True,"slots":{sid:{} for sid in siniflar},
                "eksikler":[],"sure_sn":0,"durum":"EMPTY"}

    # Aday konumlar
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
            for saat in range(1, gun_bilgi[gun]-g["boy"]+2):
                key = (gid, gun, saat)
                x[key] = True  # placeholder
                gid_adaylar[gid].append((gun, saat))

    # Asama 1: CP-SAT yerlesim
    konum, cp_durum = _asama1_yerlesim(gorevler, gid_adaylar, x, siniflar, dersler, gun_bilgi, gunler, seed, max_sure=40)
    
    basarili_1 = len(konum) > 0
    
    # Asama 2: Local search optimizasyon
    if basarili_1:
        kalan_sure = 55 - (time.time()-t0)
        if kalan_sure > 5:
            konum = _asama2_optimize(gorevler, gid_adaylar, konum, gun_bilgi, gunler, kisitlar, max_sure=min(kalan_sure, 25))

    # Sonucu slots formatina cevir
    slots = {sid:{} for sid in siniflar}
    eksikler = []
    if basarili_1:
        for g in gorevler:
            gid = g["id"]
            if gid not in konum:
                eksikler.append({"sinif":siniflar[g["sid"]].get("sinif_adi"),
                                 "ders":dersler[g["did"]].get("ders_adi"),"blok":g["boy"]})
                continue
            gun, saat = konum[gid]
            sid  = g["sid"]
            ders = dersler[g["did"]]
            if gun not in slots[sid]: slots[sid][gun] = {}
            for b in range(g["boy"]):
                slots[sid][gun][saat+b] = {
                    "ders_id":g["did"],"ders_adi":ders.get("ders_adi",""),
                    "kisa_ad":ders.get("kisa_ad",ders.get("ders_adi","")[:4]),
                    "renk":ders.get("renk","#1a6b47"),"ogretmen_tc":g["tc"],
                    "ogretmenler":g["ogrtler"],"kilitli":False
                }

    sure = time.time()-t0
    return {"basari":basarili_1,"slots":slots,"eksikler":eksikler,
            "sure_sn":round(sure,2),"durum":cp_durum,"seed":seed}


if __name__=="__main__":
    test={"siniflar":[{"id":str(i),"sinif_adi":f"9-{chr(64+i)}"} for i in range(1,4)],
          "dersler":[{"id":"101","ders_adi":"Mat","haftalik_saat":4,"blok_dagilim":[2,2],"renk":"#2563eb","kisa_ad":"MAT"},
                     {"id":"102","ders_adi":"Tur","haftalik_saat":5,"blok_dagilim":[2,2,1],"renk":"#dc2626","kisa_ad":"TUR"},
                     {"id":"103","ders_adi":"Bed","haftalik_saat":2,"blok_dagilim":[2],"renk":"#16a34a","kisa_ad":"BED"}],
          "atamalar":{str(i):[{"ders_id":"101","ogretmen_tc":"TC001","ogretmenler":[{"tc":"TC001"}]},
                               {"ders_id":"102","ogretmen_tc":"TC002","ogretmenler":[{"tc":"TC002"}]},
                               {"ders_id":"103","ogretmen_tc":"TC003","ogretmenler":[{"tc":"TC003"}]}] for i in range(1,4)},
          "kisitlar":{"TC001":{"minGunlukSaat":2,"maxGunlukSaat":6},"TC002":{"minGunlukSaat":2}},
          "gunler":[{"gun":i,"saat":8} for i in range(1,6)],"kilitli":{}}
    r=dagit(test)
    print(f"Durum:{r['durum']} Sure:{r['sure_sn']}s Eksik:{len(r['eksikler'])} Seed:{r['seed']}")
