"""
OkulYonetimSistemi - Ders Dagitim Motoru v2
OR-Tools CP-SAT. 40 sinif icin optimize edilmis.
"""
import time
from ortools.sat.python import cp_model

def dagit(veri):
    t0 = time.time()
    model = cp_model.CpModel()
    siniflar = {str(s["id"]): s for s in veri.get("siniflar", [])}
    dersler = {str(d["id"]): d for d in veri.get("dersler", [])}
    atamalar = {str(k): v for k, v in veri.get("atamalar", {}).items()}
    kisitlar = {str(k): v for k, v in veri.get("kisitlar", {}).items()}
    gun_bilgi = {int(g["gun"]): int(g["saat"]) for g in veri.get("gunler", [])}
    kilitli = veri.get("kilitli", {})
    gunler = sorted(gun_bilgi.keys())

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
                gorevler.append({"id":f"{sid}_{did}_{bi}","sid":sid,"did":did,"tc":tc,"ogrtler":atama.get("ogretmenler",[]),"boy":int(boy),"bi":bi})

    if not gorevler:
        return {"basari":True,"slots":{sid:{} for sid in siniflar},"eksikler":[],"sure_sn":0,"durum":"EMPTY"}

    # Karar degiskenleri
    x = {}
    gid_adaylar = {}
    for g in gorevler:
        gid = g["id"]
        gid_adaylar[gid] = []
        k = kisitlar.get(g["tc"],{})
        bos_gun = int(k["bosGun"]) if k.get("bosGun") else None
        kapali = [int(v) for v in k.get("kapaliGunler",[])]
        for gun in gunler:
            if bos_gun and gun == bos_gun: continue
            if gun in kapali: continue
            for saat in range(1, gun_bilgi[gun]-g["boy"]+2):
                key = (gid,gun,saat)
                x[key] = model.NewBoolVar(f"x_{gid}_{gun}_{saat}")
                gid_adaylar[gid].append((gun,saat))

    # Her gorev tam 1 yere
    for g in gorevler:
        gid = g["id"]
        av = gid_adaylar[gid]
        if av:
            model.AddExactlyOne([x[(gid,gun,saat)] for (gun,saat) in av])

    # Blok-gun kurali
    sid_did = {}
    for g in gorevler:
        sid_did.setdefault((g["sid"],g["did"]),[]).append(g)
    for (sid,did),glist in sid_did.items():
        if len(glist)<2: continue
        for gun in gunler:
            gv = []
            for g in glist:
                gid=g["id"]
                for (ag,as_) in gid_adaylar[gid]:
                    if ag==gun: gv.append(x[(gid,ag,as_)])
            if gv: model.Add(sum(gv)<=1)

    # Sinif cakismasi
    sid_g = {}
    for g in gorevler: sid_g.setdefault(g["sid"],[]).append(g)
    for sid,glist in sid_g.items():
        for gun in gunler:
            for saat in range(1,gun_bilgi[gun]+1):
                av = []
                for g in glist:
                    gid=g["id"]
                    for (ag,as_) in gid_adaylar[gid]:
                        if ag==gun and as_<=saat<as_+g["boy"]: av.append(x[(gid,ag,as_)])
                if len(av)>1: model.Add(sum(av)<=1)

    # Ogretmen cakismasi
    tc_g = {}
    for g in gorevler:
        if g["tc"]: tc_g.setdefault(g["tc"],[]).append(g)
    for tc,glist in tc_g.items():
        for gun in gunler:
            for saat in range(1,gun_bilgi[gun]+1):
                av = []
                for g in glist:
                    gid=g["id"]
                    for (ag,as_) in gid_adaylar[gid]:
                        if ag==gun and as_<=saat<as_+g["boy"]: av.append(x[(gid,ag,as_)])
                if len(av)>1: model.Add(sum(av)<=1)

    # Coz
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 55.0
    solver.parameters.num_workers = 4
    solver.parameters.log_search_progress = False
    durum = solver.Solve(model)
    sure = time.time()-t0
    basarili = durum in (cp_model.OPTIMAL, cp_model.FEASIBLE)

    slots = {sid:{} for sid in siniflar}
    eksikler = []
    if basarili:
        for g in gorevler:
            gid=g["id"]
            yerlesik=False
            for (gun,saat) in gid_adaylar[gid]:
                if solver.Value(x[(gid,gun,saat)])==1:
                    sid=g["sid"]
                    ders=dersler[g["did"]]
                    if gun not in slots[sid]: slots[sid][gun]={}
                    for b in range(g["boy"]):
                        slots[sid][gun][saat+b]={"ders_id":g["did"],"ders_adi":ders.get("ders_adi",""),"kisa_ad":ders.get("kisa_ad",ders.get("ders_adi","")[:4]),"renk":ders.get("renk","#1a6b47"),"ogretmen_tc":g["tc"],"ogretmenler":g["ogrtler"],"kilitli":False}
                    yerlesik=True; break
            if not yerlesik:
                eksikler.append({"sinif":siniflar[g["sid"]].get("sinif_adi"),"ders":dersler[g["did"]].get("ders_adi"),"blok":g["boy"]})
    return {"basari":basarili,"slots":slots,"eksikler":eksikler,"sure_sn":round(sure,2),"durum":solver.StatusName(durum)}

if __name__=="__main__":
    import json
    test={"siniflar":[{"id":str(i),"sinif_adi":f"9-{chr(64+i)}"} for i in range(1,6)],"dersler":[{"id":"101","ders_adi":"Matematik","haftalik_saat":4,"blok_dagilim":[2,2],"renk":"#2563eb","kisa_ad":"MAT"},{"id":"102","ders_adi":"Turkce","haftalik_saat":5,"blok_dagilim":[2,2,1],"renk":"#dc2626","kisa_ad":"TUR"}],"atamalar":{str(i):[{"ders_id":"101","ogretmen_tc":"TC001","ogretmenler":[{"tc":"TC001"}]},{"ders_id":"102","ogretmen_tc":"TC002","ogretmenler":[{"tc":"TC002"}]}] for i in range(1,6)},"kisitlar":{},"gunler":[{"gun":i,"saat":8} for i in range(1,6)],"kilitli":{}}
    r=dagit(test)
    print(f"Durum:{r['durum']} Sure:{r['sure_sn']}s Eksik:{len(r['eksikler'])}")
