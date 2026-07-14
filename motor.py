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
    "kilitli":  {sid: {gun: {saat: {"ders_id":..., "ogretmen_tc":...,
                                     "ogretmenler":[...]}}}}  (opsiyonel)
    "seed": int (opsiyonel)
  }

Kilitli hucreler ASLA tasinmaz/uzerine yazilmaz - once occupation gridlerine
sabit olarak yuklenir, sonra o (sinif,ders) icin kac saatin zaten kilitli
oldugu hesaplanip kalan bloklardan dusulur (JS motorundaki "kilitliSaatler"
mantigiyla birebir ayni).

Cikti CP-SAT versiyonuyla AYNI:
  {"basari":bool, "slots":{sid:{gun:{saat:{...}}}}, "eksikler":[...],
   "sure_sn":float, "durum":str, "seed":int}
"""
import time
import random


def _dagit_tek_deneme(veri):
    t0 = time.time()
    siniflar  = {str(s["id"]): s for s in veri.get("siniflar", [])}
    dersler   = {str(d["id"]): d for d in veri.get("dersler", [])}
    atamalar  = {str(k): v for k, v in veri.get("atamalar", {}).items()}
    kisitlar  = {str(k): v for k, v in veri.get("kisitlar", {}).items()}
    gun_bilgi = {int(g["gun"]): int(g["saat"]) for g in veri.get("gunler", [])}
    gunler    = sorted(gun_bilgi.keys())
    seed      = veri.get("seed", random.randint(1, 999999))
    rnd = random.Random(seed)


    # ---------------- 1. Kilitli (sabit) hucreler ----------------
    # locked_cells: [(sid, gun, saat, did, tc), ...]  - asla tasinmaz
    # locked_saat[(sid,did)] -> o ders icin zaten kilitli olan saat sayisi
    kilitli_ham = veri.get("kilitli", {}) or {}
    locked_cells = []
    locked_saat = {}
    for sid, gun_map in kilitli_ham.items():
        sid = str(sid)
        for gun, saat_map in (gun_map or {}).items():
            gun = int(gun)
            for saat, hucre in (saat_map or {}).items():
                saat = int(saat)
                did = str(hucre.get("ders_id", ""))
                tc = str(hucre.get("ogretmen_tc") or (hucre.get("ogretmenler") or [{}])[0].get("tc") or "")
                locked_cells.append((sid, gun, saat, did, tc))
                key = (sid, did)
                locked_saat[key] = locked_saat.get(key, 0) + 1

    # ---------------- 2. Gorev listesi ----------------
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
            # Ortak ders (ayni dersi birlikte veren 2+ ogretmen) - ikincil ogretmenler
            # de doluluk/kisit kontrolune girmezse cakisma olusur, bu yuzden hepsini
            # ayri bir "ek_tcler" listesinde tutup musait_mi/yerlestir/bosalt'ta
            # birlikte kontrol ediyoruz.
            tum_ogrt_tc = [str(o.get("tc") or "") for o in (atama.get("ogretmenler") or [])]
            ek_tcler = sorted(set(t for t in tum_ogrt_tc if t and t != tc))
            bloklar = list(ders.get("blok_dagilim") or [ders.get("haftalik_saat", 1)])

            # Kilitli saatleri, JS motorundaki gibi TAM BLOK olarak dus
            # (buyuk bloktan degil, listedeki sirayla - JS ile birebir tutarli)
            kalan_kilitli = locked_saat.get((sid, did), 0)
            if kalan_kilitli:
                yeni_bloklar = []
                for b in bloklar:
                    if kalan_kilitli >= b:
                        kalan_kilitli -= b
                    else:
                        yeni_bloklar.append(b)
                bloklar = yeni_bloklar

            for bi, boy in enumerate(bloklar):
                if not boy:
                    continue
                gorevler.append({
                    "id": f"{sid}_{did}_{bi}", "sid": sid, "did": did, "tc": tc,
                    "ek_tcler": ek_tcler,
                    "ogrtler": atama.get("ogretmenler", []), "boy": int(boy),
                    "placed": None,
                })

    if not gorevler:
        return {"basari": True, "slots": {sid: {} for sid in siniflar},
                "eksikler": [], "sure_sn": 0, "durum": "EMPTY", "seed": seed}

    tum_tc = sorted(set(g["tc"] for g in gorevler if g["tc"])
                     | set(t for g in gorevler for t in g["ek_tcler"])
                     | set(tc for (_, _, _, _, tc) in locked_cells if tc))
    print(f"Gorev:{len(gorevler)} Sinif:{len(siniflar)} Ogretmen:{len(tum_tc)} "
          f"Kilitli:{len(locked_cells)}", flush=True)

    # ---------------- 2. Kisit tablosu ----------------
    def kisit_al(tc):
        k = kisitlar.get(tc, {})
        kapali_saat = set()
        for kb in k.get("kapaliBosluklar", []) or []:
            try:
                kapali_saat.add((int(kb["gun"]), int(kb["saat"])))
            except (KeyError, TypeError, ValueError):
                continue
        return {
            "bosGun": int(k["bosGun"]) if k.get("bosGun") else None,
            "kapali": set(int(v) for v in k.get("kapaliGunler", [])),
            "kapaliSaat": kapali_saat,
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

    # Kilitli hucreleri sabit doluluk olarak yukle - "KILITLI" sentinel'i
    # hicbir gid'e karsilik gelmez, bu yuzden bosalt() asla dokunamaz.
    for (sid, gun, saat, did, tc) in locked_cells:
        if sid in class_occ:
            class_occ[sid][(gun, saat)] = "KILITLI"
        if tc and tc in teacher_occ:
            teacher_occ[tc][(gun, saat)] = "KILITLI"
            day_load[tc][gun] = day_load[tc].get(gun, 0) + 1
        key = (sid, did)
        gun_ders.setdefault(key, {})
        gun_ders[key][gun] = gun_ders[key].get(gun, 0) + 1

    def tum_ogrt(g):
        """Bu gorevin sorumlu oldugu TUM ogretmenler (birincil + ortak ders ise ek)."""
        if g["tc"]:
            return [g["tc"]] + g["ek_tcler"]
        return list(g["ek_tcler"])

    kayit_gunlugu = []  # [(gid, eski_placed), ...] - undo log (append-only)

    def _bosalt_ham(gid):
        g = gid_map[gid]
        if not g["placed"]:
            return
        gun, saat = g["placed"]
        ogrtler = tum_ogrt(g)
        for b in range(g["boy"]):
            class_occ[g["sid"]].pop((gun, saat + b), None)
            for tc in ogrtler:
                teacher_occ[tc].pop((gun, saat + b), None)
        key = (g["sid"], g["did"])
        if key in gun_ders and gun in gun_ders[key]:
            gun_ders[key][gun] -= 1
            if gun_ders[key][gun] <= 0:
                del gun_ders[key][gun]
        for tc in ogrtler:
            day_load[tc][gun] -= g["boy"]
        g["placed"] = None

    def _yerlestir_ham(gid, gun, saat):
        g = gid_map[gid]
        ogrtler = tum_ogrt(g)
        for b in range(g["boy"]):
            class_occ[g["sid"]][(gun, saat + b)] = gid
            for tc in ogrtler:
                teacher_occ[tc][(gun, saat + b)] = gid
        key = (g["sid"], g["did"])
        gun_ders.setdefault(key, {})
        gun_ders[key][gun] = gun_ders[key].get(gun, 0) + 1
        for tc in ogrtler:
            day_load[tc][gun] += g["boy"]
        g["placed"] = (gun, saat)

    def bosalt(gid):
        g = gid_map[gid]
        if g["placed"] is not None:
            kayit_gunlugu.append((gid, g["placed"]))
        _bosalt_ham(gid)

    def yerlestir(gid, gun, saat):
        g = gid_map[gid]
        kayit_gunlugu.append((gid, g["placed"]))
        _yerlestir_ham(gid, gun, saat)

    def musait_mi(gid, gun, saat):
        g = gid_map[gid]
        boy = g["boy"]; sid = g["sid"]; did = g["did"]
        ogrtler = tum_ogrt(g)
        if saat < 1 or saat + boy - 1 > gun_bilgi[gun]:
            return False
        for tc in ogrtler:
            k = tc_kisit[tc]
            if k["bosGun"] == gun or gun in k["kapali"]:
                return False
        for b in range(boy):
            s = saat + b
            if (gun, s) in class_occ[sid]:
                return False
            for tc in ogrtler:
                if (gun, s) in teacher_occ[tc]:
                    return False
                if (gun, s) in tc_kisit[tc]["kapaliSaat"]:
                    return False
        key = (sid, did)
        if gun_ders.get(key, {}).get(gun, 0) >= 1:
            return False  # ayni ders ayni gun tekrar olamaz
        for tc in ogrtler:
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

    def kontrol_noktasi():
        """O(1) - sadece log uzunlugunu kaydeder."""
        return len(kayit_gunlugu)

    def geri_al(nokta):
        """Log'u kontrol noktasina kadar tersten oynatarak geri alir.
        Maliyet: SADECE bu deneme sirasinda yapilan gercek islem sayisiyla
        orantili (eskiden her denemede TUM gorevlerin anlik goruntusunu
        almak O(n) idi - 900K+ islemde bu performansin %64'unu yiyordu)."""
        while len(kayit_gunlugu) > nokta:
            gid, eski_pos = kayit_gunlugu.pop()
            g = gid_map[gid]
            if g["placed"] is not None:
                _bosalt_ham(gid)
            if eski_pos is not None:
                _yerlestir_ham(gid, eski_pos[0], eski_pos[1])

    def yerlestirmeye_calis(gid, derinlik=0):
        aday = en_iyi_aday(gid)
        if aday:
            yerlestir(gid, aday[0], aday[1])
            return True
        if derinlik >= MAX_DERINLIK:
            return False

        g = gid_map[gid]
        ogrtler = tum_ogrt(g)
        key = (g["sid"], g["did"])

        for gun in gunler:
            if any(tc_kisit[tc]["bosGun"] == gun or gun in tc_kisit[tc]["kapali"] for tc in ogrtler):
                continue
            if gun_ders.get(key, {}).get(gun, 0) >= 1:
                continue
            if any(tc_kisit[tc]["maxG"] and day_load[tc][gun] + g["boy"] > tc_kisit[tc]["maxG"] for tc in ogrtler):
                continue
            for saat in range(1, gun_bilgi[gun] - g["boy"] + 2):
                cakisanlar = set()
                kilitliye_carpti = False
                for b in range(g["boy"]):
                    s = saat + b
                    occ1 = class_occ[g["sid"]].get((gun, s))
                    if occ1 == "KILITLI":
                        kilitliye_carpti = True
                        break
                    if occ1:
                        cakisanlar.add(occ1)
                    for tc in ogrtler:
                        occ2 = teacher_occ[tc].get((gun, s))
                        if occ2 == "KILITLI":
                            kilitliye_carpti = True
                            break
                        if occ2:
                            cakisanlar.add(occ2)
                    if kilitliye_carpti:
                        break
                if kilitliye_carpti:
                    continue  # kilitli hucre - asla kovulamaz, bu saati atla
                if not cakisanlar:
                    continue  # bos slot olsaydi en_iyi_aday zaten bulurdu; atla
                if len(cakisanlar) > 2:
                    continue  # cok fazla kovma riskli

                cakisanlar = sorted(cakisanlar)  # deterministik sira (set iterasyonu PYTHONHASHSEED'e bagli)
                nokta = kontrol_noktasi()
                for cg in cakisanlar:
                    bosalt(cg)

                if musait_mi(gid, gun, saat):
                    yerlestir(gid, gun, saat)
                    basarili = True
                    for cg in cakisanlar:
                        if not yerlestirmeye_calis(cg, derinlik + 1):
                            basarili = False
                            break
                    if basarili:
                        return True
                    geri_al(nokta)  # ic ice kovma zincirleri dahil TAM geri alma
                else:
                    geri_al(nokta)
        return False

    eksikler_gid = []
    for gid in kuyruk:
        if not yerlestirmeye_calis(gid):
            eksikler_gid.append(gid)

    def kovarak_yerlestir_haric(gid, haric_gun):
        """gid'i haric_gun DISINDAKI bir gune, gerekirse o gundeki bir hucreyi
        isgal edeni KOVARAK yerlestirir. gunu_tamamen_bosalt icin: dogrudan
        bos hucre bulunamadiginda (yogun/%100 dolu programlarda sikca olur)
        bu, tasinacak yer acar."""
        g = gid_map[gid]
        ogrtler_g = tum_ogrt(g)
        for gun in gunler:
            if gun == haric_gun:
                continue
            if any(tc_kisit[tc]["bosGun"] == gun or gun in tc_kisit[tc]["kapali"] for tc in ogrtler_g):
                continue
            key = (g["sid"], g["did"])
            if gun_ders.get(key, {}).get(gun, 0) >= 1:
                continue
            if any(tc_kisit[tc]["maxG"] and day_load[tc][gun] + g["boy"] > tc_kisit[tc]["maxG"]
                   for tc in ogrtler_g):
                continue
            for saat in range(1, gun_bilgi[gun] - g["boy"] + 2):
                cakisanlar = set()
                bloklanmis = False
                for b in range(g["boy"]):
                    s = saat + b
                    occ = class_occ[g["sid"]].get((gun, s))
                    if occ == "KILITLI":
                        bloklanmis = True
                        break
                    if occ:
                        cakisanlar.add(occ)
                    for otc in ogrtler_g:
                        occ2 = teacher_occ[otc].get((gun, s))
                        if occ2 == "KILITLI":
                            bloklanmis = True
                            break
                        if occ2:
                            cakisanlar.add(occ2)
                    if bloklanmis:
                        break
                if bloklanmis or not cakisanlar or len(cakisanlar) > 2:
                    continue
                nokta = kontrol_noktasi()
                for cg in sorted(cakisanlar):
                    bosalt(cg)
                if musait_mi(gid, gun, saat):
                    yerlestir(gid, gun, saat)
                    basarili = True
                    for cg in sorted(cakisanlar):
                        if not yerlestirmeye_calis(cg, MAX_DERINLIK - 1):
                            basarili = False
                            break
                    if basarili:
                        return True
                    geri_al(nokta)
                else:
                    geri_al(nokta)
        return False

    def kovarak_yerlestir_gunde(gid, hedef_gun):
        """gid'i SPECIFIK OLARAK hedef_gun'e, gerekirse o gundeki bir hucreyi
        isgal edeni KOVARAK (tam swap) yerlestirmeyi dener. kovarak_yerlestir_haric
        'herhangi bir gun (X haric)' arar, bu ise 'SADECE bu gun' hedefler -
        min-gunluk-saat doldurma icin: %100 dolu sinif programlarinda hedef
        gunde dogrudan bos hucre bulunamadiginda, o hucreyi isgal eden dersle
        YER DEGISTIRIR (o ders baska bir gune/saate tasinir)."""
        g = gid_map[gid]
        ogrtler_g = tum_ogrt(g)
        if any(tc_kisit[tc]["bosGun"] == hedef_gun or hedef_gun in tc_kisit[tc]["kapali"]
               for tc in ogrtler_g):
            return False
        key = (g["sid"], g["did"])
        if gun_ders.get(key, {}).get(hedef_gun, 0) >= 1:
            return False
        if any(tc_kisit[tc]["maxG"] and day_load[tc][hedef_gun] + g["boy"] > tc_kisit[tc]["maxG"]
               for tc in ogrtler_g):
            return False
        for saat in range(1, gun_bilgi[hedef_gun] - g["boy"] + 2):
            cakisanlar = set()
            bloklanmis = False
            for b in range(g["boy"]):
                s = saat + b
                occ = class_occ[g["sid"]].get((hedef_gun, s))
                if occ == "KILITLI":
                    bloklanmis = True
                    break
                if occ:
                    cakisanlar.add(occ)
                for otc in ogrtler_g:
                    occ2 = teacher_occ[otc].get((hedef_gun, s))
                    if occ2 == "KILITLI":
                        bloklanmis = True
                        break
                    if occ2:
                        cakisanlar.add(occ2)
                if bloklanmis:
                    break
            if bloklanmis or not cakisanlar or len(cakisanlar) > 2:
                continue
            nokta = kontrol_noktasi()
            for cg in sorted(cakisanlar):
                bosalt(cg)
            if musait_mi(gid, hedef_gun, saat):
                yerlestir(gid, hedef_gun, saat)
                basarili = True
                for cg in sorted(cakisanlar):
                    if not yerlestirmeye_calis(cg, MAX_DERINLIK - 1):
                        basarili = False
                        break
                if basarili:
                    return True
                geri_al(nokta)
            else:
                geri_al(nokta)
        return False

    def gunu_tamamen_bosalt(tc, gun):
        """tc'nin gun'deki TUM derslerini baska gunlere tasimaya calisir -
        once dogrudan bos hucre arar, olmazsa kovarak yer acar (hepsi
        basarili olursa kalici, biri bile basarisiz olursa TAM geri alir)."""
        tasklar = [g for g in gorevler if tc in tum_ogrt(g) and g["placed"] and g["placed"][0] == gun]
        if not tasklar:
            return False
        nokta = kontrol_noktasi()
        for t in tasklar:
            bosalt(t["id"])
        hepsi = True
        for t in tasklar:
            aday = en_iyi_aday(t["id"], haric_gun=gun)
            if aday:
                yerlestir(t["id"], aday[0], aday[1])
            elif kovarak_yerlestir_haric(t["id"], haric_gun=gun):
                pass
            else:
                hepsi = False
                break
        if not hepsi:
            geri_al(nokta)
            return False
        return True

    def ogrt_bos_gun_var_mi(tc):
        """tc'nin (herhangi bir sebeple - dogal, manuel bosGun, ya da onceki
        bir gecisin bosalttigi) zaten yuku SIFIR olan bir gunu var mi?"""
        return any(day_load[tc][g] == 0 for g in gunler)

    def ihlal_sayisi():
        """Su anki min-gunluk-saat ihlali sayisi (0<yuk<ming olan gun sayisi)."""
        n = 0
        for tc2 in tum_tc:
            ming2 = tc_kisit[tc2]["minG"]
            if not ming2:
                continue
            for gun2 in gunler:
                if 0 < day_load[tc2][gun2] < ming2:
                    n += 1
        return n

    # ---------------- 6. "Asla tek ders" garantisi (MUTLAK ONCELIK) ----------------
    def gunu_doldur(tc, gun, ming):
        """gun uzerindeki yuku, digerlerinden tasiyarak ming'e cikarmayi dener.
        Once dogrudan bos hucre arar, bulamazsa TAM SWAP (kovarak_yerlestir_gunde)
        dener - %100 dolu sinif programlarinda dogrudan bos hucre neredeyse hic
        olmadigindan bu adim olmadan pek cok vaka cozulemiyordu."""
        degisti = False
        adaylar_tasima = [g for g in gorevler
                           if tc in tum_ogrt(g) and g["placed"] and g["placed"][0] != gun]
        adaylar_tasima.sort(key=lambda g: -day_load[tc][g["placed"][0]])
        for t in adaylar_tasima:
            if day_load[tc][gun] >= ming:
                break
            kaynak_gun = t["placed"][0]
            kalan = day_load[tc][kaynak_gun] - t["boy"]
            if 0 < kalan < ming:
                continue  # kaynak gunu de bozar, atla
            nokta = kontrol_noktasi()
            bosalt(t["id"])
            secenekler = [s for s in adaylar(t["id"]) if s[0] == gun]
            if secenekler:
                secenekler.sort(key=lambda gs: skor(t["id"], gs[0], gs[1]))
                yerlestir(t["id"], secenekler[0][0], secenekler[0][1])
                degisti = True
            else:
                geri_al(nokta)
        return degisti

    def gunu_doldur_swap_ile(tc, gun, ming):
        """gunu_doldur basarisiz olduysa, TAM SWAP (kovarak_yerlestir_gunde)
        ile tekrar dener - dogrudan bos hucre bulunamayan yogun/%100 dolu
        programlarda bu, iki dersin yer degistirmesiyle yer acar."""
        degisti = False
        adaylar_tasima = [g for g in gorevler
                           if tc in tum_ogrt(g) and g["placed"] and g["placed"][0] != gun]
        adaylar_tasima.sort(key=lambda g: -day_load[tc][g["placed"][0]])
        for t in adaylar_tasima:
            if day_load[tc][gun] >= ming:
                break
            kaynak_gun = t["placed"][0]
            kalan = day_load[tc][kaynak_gun] - t["boy"]
            if 0 < kalan < ming:
                continue
            nokta = kontrol_noktasi()
            bosalt(t["id"])
            if kovarak_yerlestir_gunde(t["id"], gun):
                degisti = True
            else:
                geri_al(nokta)
        return degisti

    def tek_ders_yasakla_pass():
        """'Asla tek ders / gunde minGunlukSaat altinda ders olmasin' - BU
        KURAL MUTLAKTIR, bos gun tercihinden ONCELIKLIDIR. Her ihlal icin
        once DOLDURMAYI (dogrudan), sonra DOLDURMAYI (swap ile), olmazsa
        KOSULSUZ TAMAMEN BOSALTMAYI dener (gerekirse 2. bir bos gun pahasina
        bile olsa - tek ders kuralinin istisnasi yoktur). Bir degisiklik
        baskasini tetikleyebilecegi icin degisiklik kalmayana ya da
        MAX_TUR'a kadar tekrarlar."""
        MAX_TUR = 10
        for _ in range(MAX_TUR):
            degisti = False
            for tc in tum_tc:
                ming = tc_kisit[tc]["minG"]
                if not ming:
                    continue
                for gun in gunler:
                    yuk = day_load[tc][gun]
                    if not (0 < yuk < ming):
                        continue
                    if gunu_doldur(tc, gun, ming):
                        degisti = True
                    elif gunu_doldur_swap_ile(tc, gun, ming):
                        degisti = True
                    elif gunu_tamamen_bosalt(tc, gun):
                        degisti = True
            if not degisti:
                break

    tek_ders_yasakla_pass()

    # ---------------- 7. Otomatik bos gun atama (ISTEGE BAGLI - tek-ders kuralini ASLA bozmaz) ----------------
    def otomatik_bos_gun_pass():
        """Manuel bosGun'u OLMAYAN ve HENUZ hicbir bos gunu olmayan
        ogretmenler icin otomatik bir bos gun olusturmaya CALISIR (bazi
        ogretmenler icin bu mumkun olmayabilir - bu normal, herkese bos gun
        garanti edilmez). TUM gunleri en-az-yuklu'den en-cok-yuklu'ye dener.
        GUVENLIK: her denemeden sonra toplam tek-ders ihlali sayisini
        kontrol eder - eger bu bos gun denemesi YENI bir ihlale yol actiysa
        KESIN GERI ALINIR ve bir sonraki gun adayi denenir. Boylece bos gun
        ozelligi asla 'asla tek ders' kuralini bozamaz."""
        for tc in tum_tc:
            if tc_kisit[tc]["bosGun"] is not None:
                continue  # manuel bosGun'a asla dokunma
            if ogrt_bos_gun_var_mi(tc):
                continue  # zaten (dogal ya da tek-ders duzeltmesinden) bir bos gunu var
            calisilan_gunler = [g for g in gunler if day_load[tc][g] > 0]
            if len(calisilan_gunler) <= 1:
                continue
            adaylar_gun = sorted(calisilan_gunler, key=lambda g: day_load[tc][g])
            for aday_gun in adaylar_gun:
                once = ihlal_sayisi()
                nokta = kontrol_noktasi()
                if gunu_tamamen_bosalt(tc, aday_gun):
                    if ihlal_sayisi() > once:
                        geri_al(nokta)  # yeni tek-ders ihlali yaratti - kabul edilemez
                        continue
                    break  # basarili VE tek-ders kuralini bozmadi

    otomatik_bos_gun_pass()

    # ---------------- 7b. Son tek-ders temizligi (bos gun gecisi yan etki yaratmis olabilir) ----------------
    tek_ders_yasakla_pass()

    # ---------------- 8. Pencere minimizasyonu (hedef: haftalik <=2 pencere) ----------------
    MAX_PENCERE_HEDEF = 2

    def ogrt_gun_saatleri(tc, gun):
        saatler = []
        for g in gorevler:
            if tc in tum_ogrt(g) and g["placed"] and g["placed"][0] == gun:
                saatler.extend(range(g["placed"][1], g["placed"][1] + g["boy"]))
        return sorted(saatler)

    def ogrt_haftalik_pencere(tc):
        toplam = 0
        for gun in gunler:
            saatler = ogrt_gun_saatleri(tc, gun)
            if len(saatler) < 2:
                continue
            toplam += (max(saatler) - min(saatler) + 1) - len(saatler)
        return toplam

    def gun_ici_sikistir(tc, gun):
        """Bir gun icindeki dagilmis dersleri sola dogru sikistirir."""
        degisti_toplam = False
        for _ic_tur in range(8):
            tasklar = sorted(
                [g for g in gorevler if tc in tum_ogrt(g) and g["placed"] and g["placed"][0] == gun],
                key=lambda g: g["placed"][1])
            if len(tasklar) < 2:
                break
            degisti = False
            for t in tasklar:
                gun2, saat2 = t["placed"]
                if saat2 <= 1:
                    continue
                nokta = kontrol_noktasi()
                bosalt(t["id"])
                if musait_mi(t["id"], gun2, saat2 - 1):
                    yerlestir(t["id"], gun2, saat2 - 1)
                    degisti = True
                    degisti_toplam = True
                else:
                    geri_al(nokta)
            if not degisti:
                break
        return degisti_toplam

    def gunler_arasi_bosluk_doldur(tc):
        """Bir gunun ic bosluguna, tc'nin BASKA bir gundeki bir dersini tasimayi
        dener. Once dogrudan bos hucre arar; sinif dolu oldugu icin bos hucre
        yoksa, hedef hucreyi isgal edeni KOVUP (mevcut yerlestirmeye_calis
        makinesiyle) yeniden yerlestirmeyi dener - yogun dolu programlarda
        bos hucre bulmak neredeyse imkansiz oldugundan bu adim olmadan
        pencere azaltma pratikte hicbir sey yapamiyordu."""
        degisti = False
        for gun in gunler:
            saatler = ogrt_gun_saatleri(tc, gun)
            if len(saatler) < 2:
                continue
            mn, mx = min(saatler), max(saatler)
            bos_saatler = [s for s in range(mn, mx + 1) if s not in saatler]
            if not bos_saatler:
                continue
            digerleri = [g for g in gorevler
                         if tc in tum_ogrt(g) and g["placed"] and g["placed"][0] != gun]
            digerleri.sort(key=lambda g: -g["boy"])
            for t in digerleri:
                boy = t["boy"]
                hedef_basla = None
                for i in range(len(bos_saatler) - boy + 1):
                    aday = bos_saatler[i:i + boy]
                    if aday == list(range(aday[0], aday[0] + boy)):
                        hedef_basla = aday[0]
                        break
                if hedef_basla is None:
                    continue

                nokta = kontrol_noktasi()
                bosalt(t["id"])

                if musait_mi(t["id"], gun, hedef_basla):
                    yerlestir(t["id"], gun, hedef_basla)
                    degisti = True
                    break

                # Dogrudan bos degil - hedef hucreyi isgal edeni kovmayi dene
                ogrtler_t = tum_ogrt(t)
                cakisanlar = set()
                bloklanmis = False
                for b in range(boy):
                    s = hedef_basla + b
                    occ = class_occ[t["sid"]].get((gun, s))
                    if occ == "KILITLI":
                        bloklanmis = True
                        break
                    if occ:
                        cakisanlar.add(occ)
                    for otc in ogrtler_t:
                        occ2 = teacher_occ[otc].get((gun, s))
                        if occ2 == "KILITLI":
                            bloklanmis = True
                            break
                        if occ2:
                            cakisanlar.add(occ2)
                    if bloklanmis:
                        break

                if bloklanmis or not cakisanlar or len(cakisanlar) > 2:
                    geri_al(nokta)
                    continue

                for cg in sorted(cakisanlar):
                    bosalt(cg)
                if musait_mi(t["id"], gun, hedef_basla):
                    yerlestir(t["id"], gun, hedef_basla)
                    basarili = True
                    for cg in sorted(cakisanlar):
                        if not yerlestirmeye_calis(cg, MAX_DERINLIK - 1):
                            basarili = False
                            break
                    if basarili:
                        degisti = True
                        break
                    geri_al(nokta)
                else:
                    geri_al(nokta)
        return degisti

    def pencere_azalt_pass():
        """MAX_PENCERE_HEDEF'e ulasmaya calisan best-effort local search.
        Agir kisit yuklerinde tam garanti VEREMEZ ama mumkun oldugunca
        yaklasir. Once en cok pencereli ogretmenden baslar."""
        for _dis_tur in range(6):
            pencereli = sorted(
                (tc for tc in tum_tc if ogrt_haftalik_pencere(tc) > MAX_PENCERE_HEDEF),
                key=lambda tc: -ogrt_haftalik_pencere(tc))
            if not pencereli:
                break
            herhangi_degisti = False
            for tc in pencereli:
                for gun in gunler:
                    if gun_ici_sikistir(tc, gun):
                        herhangi_degisti = True
                if gunler_arasi_bosluk_doldur(tc):
                    herhangi_degisti = True
            if not herhangi_degisti:
                break

    pencere_azalt_pass()

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

    # ---- Kalite istatistikleri (coklu-deneme sarmalayicisi icin) ----
    min_ihlal_sayisi = 0
    for tc in tum_tc:
        ming = tc_kisit[tc]["minG"]
        if not ming:
            continue
        for gun in gunler:
            if 0 < day_load[tc][gun] < ming:
                min_ihlal_sayisi += 1
    pencere_toplam = sum(ogrt_haftalik_pencere(tc) for tc in tum_tc)
    pencere_fazla_sayisi = sum(1 for tc in tum_tc if ogrt_haftalik_pencere(tc) > MAX_PENCERE_HEDEF)

    print(f"Tamamlandi {sure}s eksik={len(eksikler)} min_ihlal={min_ihlal_sayisi} "
          f"pencere_fazla={pencere_fazla_sayisi} pencere_toplam={pencere_toplam}", flush=True)

    return {"basari": basarili, "slots": slots, "eksikler": eksikler,
            "sure_sn": sure, "durum": durum, "seed": seed,
            "istatistik": {
                "min_ihlal_sayisi": min_ihlal_sayisi,
                "pencere_fazla_sayisi": pencere_fazla_sayisi,
                "pencere_toplam": pencere_toplam,
            }}


def dagit(veri, kac_deneme=5, zaman_siniri_sn=45):
    """Coklu-deneme sarmalayicisi: _dagit_tek_deneme'yi farkli (ama
    deterministik) seed'lerle birden fazla kez calistirir, her sonucu
    kalite skoruna gore kiyaslar ve en iyisini dondurur.

    Oncelik sirasi (skor ne kadar dusukse o kadar iyi):
      1) eksik ders sayisi (EN AGIR - sinif ders eksik kalmasin)
      2) min gunluk saat ihlali (asla tek ders)
      3) >2 pencereli ogretmen sayisi
      4) toplam pencere saati (ince ayar)

    app.py TARAFINDA HICBIR DEGISIKLIK GEREKMEZ - 'from motor import dagit'
    aynen calismaya devam eder, sadece capraz coklu deneme ile daha iyi
    sonuc dondurur.
    """
    taban_seed = veri.get("seed", random.randint(1, 999999))
    t_baslangic = time.time()
    en_iyi = None
    en_iyi_skor = None

    for i in range(kac_deneme):
        deneme_veri = dict(veri)
        deneme_veri["seed"] = taban_seed + i * 7919  # her deneme farkli/deterministik seed
        sonuc = _dagit_tek_deneme(deneme_veri)
        ist = sonuc.get("istatistik", {})
        skor = (
            len(sonuc["eksikler"]) * 1_000_000
            + ist.get("min_ihlal_sayisi", 0) * 10_000
            + ist.get("pencere_fazla_sayisi", 0) * 100
            + ist.get("pencere_toplam", 0)
        )
        print(f"[deneme {i+1}/{kac_deneme}] seed={deneme_veri['seed']} skor={skor}", flush=True)
        if en_iyi is None or skor < en_iyi_skor:
            en_iyi = sonuc
            en_iyi_skor = skor
        if skor == 0:
            break  # mukemmel sonuc bulundu, daha fazla denemeye gerek yok
        if time.time() - t_baslangic > zaman_siniri_sn:
            print("Zaman siniri asildi, en iyi sonucla devam ediliyor", flush=True)
            break

    en_iyi["seed"] = taban_seed  # disariya orijinal seed'i raporla
    return en_iyi
