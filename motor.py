"""
OkulYonetimSistemi — Ders Dağıtım Motoru
OR-Tools CP-SAT constraint programming solver.

Giriş (JSON):
{
  "siniflar":   [...],  # [{id, sinif_adi}]
  "dersler":    [...],  # [{id, ders_adi, haftalik_saat, blok_dagilim, renk, kisa_ad}]
  "atamalar":   {...},  # {sinif_id: [{ders_id, ogretmen_tc, ogretmenler, ...}]}
  "kisitlar":   {...},  # {tc: {bosGun, kapaliGunler, maxGunlukSaat, minGunlukSaat}}
  "gunler":     [...],  # [{gun: 1..5, saat: 8}]  (1=Pzt, 5=Cum)
  "kilitli":    {...},  # {sinif_id: {gun: {saat: {ders_id, ogretmen_tc}}}}
}

Çıkış (JSON):
{
  "basari": true/false,
  "slots":  {sinif_id: {gun: {saat: {ders_id, ogretmen_tc, ...}}}},
  "eksikler": [...],  # yerleşemeyen dersler
  "sure_sn": float
}
"""

import time
from ortools.sat.python import cp_model


def dagit(veri: dict) -> dict:
    t0 = time.time()
    model = cp_model.CpModel()

    # ── Veriyi ayrıştır ──────────────────────────────────────────
    siniflar   = {str(s["id"]): s for s in veri.get("siniflar", [])}
    dersler    = {str(d["id"]): d for d in veri.get("dersler", [])}
    atamalar   = {str(k): v for k, v in veri.get("atamalar", {}).items()}
    kisitlar   = {str(k): v for k, v in veri.get("kisitlar", {}).items()}
    gun_bilgi  = {g["gun"]: g["saat"] for g in veri.get("gunler", [])}
    kilitli    = veri.get("kilitli", {})

    gunler     = sorted(gun_bilgi.keys())
    MAX_SAAT   = max(gun_bilgi.values(), default=8)

    # ── Görev listesi ────────────────────────────────────────────
    # Her görev = bir blok (örn. 5 saatlik ders [2,2,1] → 3 görev)
    gorevler = []  # {id, sid, ders_id, tc, ogretmenler, boy, blok_idx}
    for sid, atama_list in atamalar.items():
        if sid not in siniflar:
            continue
        for atama in atama_list:
            did = str(atama.get("ders_id", ""))
            if did not in dersler:
                continue
            ders   = dersler[did]
            tc     = str(atama.get("ogretmen_tc") or
                         (atama.get("ogretmenler") or [{}])[0].get("tc") or "")
            ogrtler = atama.get("ogretmenler", [])
            bloklar = ders.get("blok_dagilim") or [ders.get("haftalik_saat", 1)]
            for bi, boy in enumerate(bloklar):
                if not boy:
                    continue
                gorevler.append({
                    "id":  f"{sid}_{did}_{bi}",
                    "sid": sid, "did": did,
                    "tc":  tc, "ogrtler": ogrtler,
                    "boy": int(boy), "bi": bi
                })

    # ── Karar değişkenleri ───────────────────────────────────────
    # x[g_id][gun][saat] = BoolVar  (1 → bu görev bu gün/saatte başlıyor)
    x = {}
    for g in gorevler:
        gid = g["id"]
        x[gid] = {}
        for gun in gunler:
            x[gid][gun] = {}
            max_s = gun_bilgi[gun]
            for saat in range(1, max_s - g["boy"] + 2):
                x[gid][gun][saat] = model.NewBoolVar(f"x_{gid}_{gun}_{saat}")

    # ── Hard kısıt 1: Her görev tam olarak bir yere yerleşmeli ──
    zorunlu = []  # yerleşemezse penalty
    for g in gorevler:
        gid = g["id"]
        tum_secenekler = [
            x[gid][gun][saat]
            for gun in gunler
            for saat in x[gid][gun]
        ]
        if not tum_secenekler:
            continue
        # Kilitli slot var mı?
        kilitli_sid = kilitli.get(g["sid"], {})
        kilitli_var = None
        for gun, gsaatler in kilitli_sid.items():
            for saat, sl in gsaatler.items():
                if str(sl.get("ders_id")) == g["did"]:
                    gun_i, saat_i = int(gun), int(saat)
                    if gun_i in x[gid] and saat_i in x[gid][gun_i]:
                        kilitli_var = x[gid][gun_i][saat_i]
                        model.Add(kilitli_var == 1)

        if kilitli_var:
            model.Add(sum(tum_secenekler) == 1)
        else:
            # Soft: yerleşmeye çalış, zorla değil
            p = model.NewBoolVar(f"p_{gid}")
            model.Add(sum(tum_secenekler) == 1).OnlyEnforceIf(p)
            model.Add(sum(tum_secenekler) == 0).OnlyEnforceIf(p.Not())
            zorunlu.append(p)

    # ── Hard kısıt 2: Sınıf çakışması ───────────────────────────
    sid_gorev = {}
    for g in gorevler:
        sid_gorev.setdefault(g["sid"], []).append(g)

    for sid, glist in sid_gorev.items():
        for gun in gunler:
            max_s = gun_bilgi[gun]
            for saat in range(1, max_s + 1):
                # Bu sınıf bu saatte en fazla 1 görev
                bolum = []
                for g in glist:
                    gid = g["id"]
                    for bas in range(max(1, saat - g["boy"] + 1), saat + 1):
                        if bas in x[gid].get(gun, {}):
                            bolum.append(x[gid][gun][bas])
                if bolum:
                    model.Add(sum(bolum) <= 1)

    # ── Hard kısıt 3: Öğretmen çakışması ─────────────────────────
    tc_gorev = {}
    for g in gorevler:
        if g["tc"]:
            tc_gorev.setdefault(g["tc"], []).append(g)

    for tc, glist in tc_gorev.items():
        for gun in gunler:
            max_s = gun_bilgi[gun]
            for saat in range(1, max_s + 1):
                bolum = []
                for g in glist:
                    gid = g["id"]
                    for bas in range(max(1, saat - g["boy"] + 1), saat + 1):
                        if bas in x[gid].get(gun, {}):
                            bolum.append(x[gid][gun][bas])
                if bolum:
                    model.Add(sum(bolum) <= 1)

    # ── Hard kısıt 4: Öğretmen boş günü ──────────────────────────
    for g in gorevler:
        tc = g["tc"]
        if not tc:
            continue
        k = kisitlar.get(tc, {})
        bos_gun = k.get("bosGun")
        kapali  = k.get("kapaliGunler", [])
        for gun in gunler:
            engellensin = (bos_gun and int(bos_gun) == gun) or (gun in kapali)
            if engellensin:
                for saat in x[g["id"]].get(gun, {}):
                    model.Add(x[g["id"]][gun][saat] == 0)

    # ── Hard kısıt 5: Blok-gün (aynı dersin farklı blokları farklı günde) ──
    for sid, glist in sid_gorev.items():
        did_bloklar = {}
        for g in glist:
            did_bloklar.setdefault(g["did"], []).append(g)
        for did, blist in did_bloklar.items():
            if len(blist) < 2:
                continue
            for gun in gunler:
                # Bu günde bu dersin en fazla 1 bloğu yerleşebilir
                gun_vars = []
                for g in blist:
                    gid = g["id"]
                    for saat in x[gid].get(gun, {}):
                        gun_vars.append(x[gid][gun][saat])
                if gun_vars:
                    model.Add(sum(gun_vars) <= 1)

    # ── Soft kısıt: Öğretmen günlük max/min ders (penalty ile) ──
    pencere_cezalar = []
    max_gun_cezalar = []

    for tc, glist in tc_gorev.items():
        k = kisitlar.get(tc, {})
        max_gun = k.get("maxGunlukSaat", 8)
        min_gun = k.get("minGunlukSaat", 0)

        for gun in gunler:
            # Bu öğretmenin bu gündeki toplam saati
            gun_vars = []
            for g in glist:
                gid = g["id"]
                for saat in x[gid].get(gun, {}):
                    for b in range(g["boy"]):
                        gun_vars.append(x[gid][gun][saat])
                    # blok başladığında boy kadar saat işgal eder
                    # → sadece başlangıç var'ını say, × boy değil; ama toplam saat
            # Daha doğru: her saati ayrı say
            # (zaten çakışma kısıtı sayesinde toplam unique saatler = sum vars)
            saat_toplam = []
            for g in glist:
                gid = g["id"]
                for bas in x[gid].get(gun, {}):
                    for b in range(g["boy"]):
                        s = bas + b
                        if s <= gun_bilgi[gun]:
                            saat_toplam.append(x[gid][gun][bas])

            if not saat_toplam:
                continue

            # Max günlük ihlal
            asim = model.NewIntVar(0, 10, f"asim_{tc}_{gun}")
            model.Add(asim >= sum(saat_toplam) - max_gun)
            model.Add(asim >= 0)
            max_gun_cezalar.append(asim)

    # ── Soft kısıt: Pencere (öğretmenin gün içi boşluğu) ─────────
    # Pencere = ilk ve son ders arasındaki boş saatler
    # OR-Tools'ta doğrudan modellemek zor, proxy: "ardışıklık bonusu" yerine
    # "günde aynı öğretmenin saatleri arasındaki boşluk" minimize et
    # Bunu basit tutalım: max günlük saati zorla sınırla, pencere post-processing'e bırak

    # ── Amaç: tüm dersleri yerleştir, soft ihlalleri minimize et ──
    hedef_parcalar = []
    if zorunlu:
        # Yerleşen görev sayısını maximize et (= eksikleri minimize et)
        hedef_parcalar.append(10000 * sum(zorunlu))  # max 10000 × görev sayısı
    for c in max_gun_cezalar:
        hedef_parcalar.append(-c)  # max günlük aşımı minimize

    if hedef_parcalar:
        model.Maximize(sum(hedef_parcalar))

    # ── Çöz ──────────────────────────────────────────────────────
    solver  = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 60.0  # 60 saniye limit
    solver.parameters.num_workers = 8              # çok thread
    solver.parameters.log_search_progress = False

    durum = solver.Solve(model)
    sure  = time.time() - t0

    basarili = durum in (cp_model.OPTIMAL, cp_model.FEASIBLE)

    # ── Sonucu slots formatına çevir ──────────────────────────────
    slots = {sid: {} for sid in siniflar}
    eksikler = []

    if basarili:
        for g in gorevler:
            gid = g["id"]
            yerlesik = False
            for gun in gunler:
                for saat, bv in x[gid].get(gun, {}).items():
                    if solver.Value(bv) == 1:
                        sid = g["sid"]
                        ders = dersler[g["did"]]
                        tc   = g["tc"]
                        if gun not in slots[sid]:
                            slots[sid][gun] = {}
                        for b in range(g["boy"]):
                            slots[sid][gun][saat + b] = {
                                "ders_id":     g["did"],
                                "ders_adi":    ders.get("ders_adi", ""),
                                "kisa_ad":     ders.get("kisa_ad", ders.get("ders_adi","")[:4]),
                                "renk":        ders.get("renk", "#1a6b47"),
                                "ogretmen_tc": tc,
                                "ogretmenler": g["ogrtler"],
                                "kilitli":     False
                            }
                        yerlesik = True
                        break
                if yerlesik:
                    break
            if not yerlesik:
                eksikler.append({
                    "sinif": siniflar[g["sid"]].get("sinif_adi"),
                    "ders":  dersler[g["did"]].get("ders_adi"),
                    "blok":  g["boy"]
                })

    return {
        "basari":  basarili,
        "slots":   slots,
        "eksikler": eksikler,
        "sure_sn": round(sure, 2),
        "durum":   solver.StatusName(durum)
    }


if __name__ == "__main__":
    # Basit test
    import json
    test = {
        "siniflar": [{"id": 1, "sinif_adi": "9-A"}],
        "dersler": [
            {"id": 101, "ders_adi": "Matematik", "haftalik_saat": 4,
             "blok_dagilim": [2, 2], "renk": "#2563eb", "kisa_ad": "MAT"},
            {"id": 102, "ders_adi": "Türkçe", "haftalik_saat": 3,
             "blok_dagilim": [2, 1], "renk": "#dc2626", "kisa_ad": "TUR"},
        ],
        "atamalar": {"1": [
            {"ders_id": 101, "ogretmen_tc": "TC001", "ogretmenler": [{"tc": "TC001"}]},
            {"ders_id": 102, "ogretmen_tc": "TC002", "ogretmenler": [{"tc": "TC002"}]},
        ]},
        "kisitlar": {},
        "gunler": [{"gun": i, "saat": 8} for i in range(1, 6)],
        "kilitli": {}
    }
    sonuc = dagit(test)
    print(json.dumps(sonuc, ensure_ascii=False, indent=2))
