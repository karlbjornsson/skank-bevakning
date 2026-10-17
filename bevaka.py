"""Bevakar SKÄNK på Auctionet (utvalda hus), Blocket (Stockholm) och Tradera (Stockholm).

Kör:  python bevaka.py                   -> visar nya annonser sedan förra körningen + skriver rapport.html
      python bevaka.py --alla            -> visar alla träffar, inte bara nya
      python bevaka.py --kallor blocket  -> kollar bara Blocket (kommaseparerat: auctionet,blocket,tradera)
      python bevaka.py --oppna           -> öppnar rapporten i webbläsaren om något nytt finns

Mejl skickas om miljövariablerna GMAIL_USER, GMAIL_APP_PASSWORD och MAIL_TO är satta.
"""
import html
import json
import os
import re
import smtplib
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    SVERIGE = ZoneInfo("Europe/Stockholm")
except Exception:  # t.ex. Windows utan tzdata – använd datorns lokala tid
    SVERIGE = None

SOKORD = "skänk"
AUCTIONET_HUS = {
    239: "Stockholms Auktionsverk Magasin 5",
    232: "Crafoord Auktioner Stockholm",
    240: "Stockholms Auktionsverk Sickla",
}
BLOCKET_STOCKHOLM = "0.300001"  # Stockholms län
BLOCKET_SIDOR = 3
TRADERA_LAN = "Stockholm"
# Titeln måste innehålla ordet (sökmotorerna matchar annars t.ex. "bortskänkes" och beskrivningstext)
TITELFILTER = re.compile(r"\b(skänk(?!es\b)|sideboard)", re.I)  # "skänkes" = ges bort

MAPP = Path(__file__).resolve().parent
STATE = MAPP / "sedda.json"
RAPPORT = MAPP / "rapport.html"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"


def tid(ts):
    return datetime.fromtimestamp(ts, SVERIGE)


def hamta(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "sv-SE"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def auctionet():
    ut = []
    for cid, hus in AUCTIONET_HUS.items():
        q = urllib.parse.urlencode({"q": SOKORD, "company_id": cid, "per_page": 100})
        data = json.loads(hamta(f"https://auctionet.com/api/v2/items.json?{q}"))
        for i in data["items"]:
            if i["company_id"] != cid:
                continue
            bud = i.get("highest_bid_amount") or i.get("next_bid_amount")
            ut.append({
                "id": f"auctionet:{i['id']}",
                "kalla": f"Auctionet – {hus}",
                "titel": i["title"],
                "pris": f"Utrop {i.get('estimate') or '?'} kr, bud {bud} kr",
                "info": "Slutar " + tid(i["ends_at"]).strftime("%Y-%m-%d %H:%M"),
                "url": i["url"].replace("/en/", "/sv/"),
                "bild": (i.get("images") or [{}])[0].get("thumb", ""),
            })
    return ut


def blocket():
    ut = []
    for sida in range(1, BLOCKET_SIDOR + 1):
        q = urllib.parse.urlencode({"q": SOKORD, "location": BLOCKET_STOCKHOLM,
                                    "sort": "PUBLISHED_DESC", "page": sida})
        data = json.loads(hamta(
            f"https://www.blocket.se/recommerce/forsale/search/api/search/SEARCH_ID_BAP_COMMON?{q}"))
        for d in data.get("docs", []):
            pris = (d.get("price") or {}).get("amount")
            ut.append({
                "id": f"blocket:{d['id']}",
                "kalla": "Blocket",
                "titel": d.get("heading", ""),
                "pris": f"{pris} kr" if pris is not None else "",
                "info": f"{d.get('location', '')}, "
                        + tid(d.get("timestamp", 0) / 1000).strftime("%Y-%m-%d %H:%M"),
                "url": d.get("canonical_url", ""),
                "bild": (d.get("image") or {}).get("url", ""),
            })
        if not data.get("docs"):
            break
    return ut


def tradera():
    q = urllib.parse.urlencode({"q": SOKORD, "county": TRADERA_LAN, "sortBy": "AddedOn"})
    sida = hamta(f"https://www.tradera.com/search?{q}")
    # Sökresultaten ligger som JSON-strängar i Next.js-datan (self.__next_f.push)
    flight = "".join(json.loads(s) for s in re.findall(
        r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', sida))
    pos = flight.find('"totalItemCount"')
    pos = flight.find('"items":[', pos)
    if pos < 0:
        raise RuntimeError("hittade ingen resultatlista på Tradera")
    items, _ = json.JSONDecoder().raw_decode(flight, pos + len('"items":'))
    ut = []
    for i in items:
        if "itemId" not in i:
            continue
        bild = (i.get("imageUrlTemplate") or "").replace("{format}", "images")
        ut.append({
            "id": f"tradera:{i['itemId']}",
            "kalla": "Tradera",
            "titel": i.get("shortDescription", ""),
            "pris": f"{i.get('price')} kr",
            "info": i.get("endDate", "") and "Slutar " + str(i.get("endDate"))[:16].replace("T", " "),
            "url": i.get("itemUrl", ""),
            "bild": bild,
        })
    return ut


def skriv_rapport(alla, nya_id):
    rader = []
    for a in sorted(alla, key=lambda a: (a["id"] not in nya_id, a["kalla"])):
        ny = a["id"] in nya_id
        rader.append(
            f'<tr class="{"ny" if ny else ""}"><td>{"NY" if ny else ""}</td>'
            f'<td>{f"<img src={html.escape(a["bild"])!r} loading=lazy>" if a["bild"] else ""}</td>'
            f'<td><a href="{html.escape(a["url"])}" target="_blank">{html.escape(a["titel"])}</a></td>'
            f'<td>{html.escape(a["kalla"])}</td><td>{html.escape(a["pris"])}</td>'
            f'<td>{html.escape(a["info"] or "")}</td></tr>')
    RAPPORT.write_text(f"""<!doctype html><meta charset="utf-8"><title>Skänkbevakning</title>
<style>body{{font-family:system-ui;margin:20px}}table{{border-collapse:collapse;width:100%}}
td{{padding:6px;border-bottom:1px solid #ddd;vertical-align:middle}}img{{width:80px;height:80px;object-fit:cover}}
tr.ny{{background:#fff6d6}}td:first-child{{font-weight:bold;color:#c00}}</style>
<h1>Skänkbevakning</h1><p>Uppdaterad {datetime.now():%Y-%m-%d %H:%M} – {len(nya_id)} nya, {len(alla)} totalt.</p>
<table>{''.join(rader)}</table>""", encoding="utf-8")


def skicka_mejl(nya):
    anvandare = os.environ.get("GMAIL_USER")
    losen = os.environ.get("GMAIL_APP_PASSWORD")
    till = os.environ.get("MAIL_TO", anvandare)
    if not (anvandare and losen and till):
        return
    kort = "".join(
        f'<div style="margin-bottom:16px">'
        f'{f"<img src={html.escape(a["bild"])!r} width=120 style=float:left;margin-right:12px>" if a["bild"] else ""}'
        f'<a href="{html.escape(a["url"])}"><b>{html.escape(a["titel"])}</b></a><br>'
        f'{html.escape(a["kalla"])}<br>{html.escape(a["pris"])}<br>{html.escape(a["info"] or "")}'
        f'<div style="clear:both"></div></div>' for a in nya)
    msg = MIMEText(f'<div style="font-family:sans-serif">{kort}</div>', "html", "utf-8")
    forsta = nya[0]["titel"][:60]
    msg["Subject"] = (f"Ny skänk: {forsta}" if len(nya) == 1
                      else f"{len(nya)} nya skänkar – {forsta} m.fl.")
    msg["From"] = anvandare
    msg["To"] = till
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(anvandare, losen)
        s.sendmail(anvandare, [t.strip() for t in till.split(",")], msg.as_string())
    print(f"Mejl skickat till {till}")


def main():
    visa_alla = "--alla" in sys.argv
    valda = {"auctionet", "blocket", "tradera"}
    if "--kallor" in sys.argv:
        valda = set(sys.argv[sys.argv.index("--kallor") + 1].lower().split(","))
    sedda = set(json.loads(STATE.read_text(encoding="utf-8"))) if STATE.exists() else set()
    alla, nya = [], []
    for namn, f in [("auctionet", auctionet), ("blocket", blocket), ("tradera", tradera)]:
        if namn not in valda:
            continue
        try:
            r = [a for a in f() if TITELFILTER.search(a["titel"])]
        except Exception as e:
            print(f"{namn}: FEL – {e}")
            continue
        # Första gången en källa körs markeras allt som sett, så att det inte blir ett jättemejl
        forsta = not any(s.startswith(namn + ":") for s in sedda)
        n = [] if forsta else [a for a in r if a["id"] not in sedda]
        print(f"{namn}: {len(r)} träffar, {len(n)} nya" + (" (första körningen)" if forsta else ""))
        sedda |= {a["id"] for a in r}
        alla += r
        nya += n
        time.sleep(1)
    nya_id = {a["id"] for a in nya}
    print(f"\n{len(nya)} nya annonser")
    for a in (alla if visa_alla else nya):
        print(f"- [{a['kalla']}] {a['titel']} | {a['pris']} | {a['info']}\n  {a['url']}")
    skriv_rapport(alla, nya_id)
    STATE.write_text(json.dumps(sorted(sedda), indent=0), encoding="utf-8")
    if nya:
        skicka_mejl(nya)
    print(f"\nRapport: {RAPPORT}")
    if "--oppna" in sys.argv and nya:
        webbrowser.open(RAPPORT.as_uri())


if __name__ == "__main__":
    main()
