# Seed a realistic sample so dashboard.html renders before the first live `update`.
import store
from datetime import datetime, timedelta
store.init()
now = datetime.utcnow()
recent = (now - timedelta(days=2)).isoformat(timespec="seconds")   # -> shows NEW badge
older  = (now - timedelta(days=40)).isoformat(timespec="seconds")
sample = [
 ("Culture Moves Europe — Individual Mobility","Creative Europe / Goethe-Institut","https://www.goethe.de/en/kul/foe/cmm.html","On the Move","Rolling monthly mobility grant, €85/day + travel. Reopens autumn 2026.",None,"EU","Mobility","likely","€85/day",older),
 ("KUNSTFONDS_Stipendium 2027","Stiftung Kunstfonds","https://www.kunstfonds.de","Kunstfonds","Stipendium 18.000 EUR for freelance visual artists in Germany. Deadline 15 January 2027.","2027-01-15","DE","Grant","likely","18.000 EUR",older),
 ("Rijksakademie Residency 2028","Rijksakademie","https://rijksakademie.nl","On the Move","Two-year Amsterdam residency, monthly stipend €19,800/yr + materials. Deadline 1 February 2027.","2027-02-01","EU","Residency","likely","€19,800/yr",recent),
 ("Gasworks Residency (open call)","Gasworks London","https://www.gasworks.org.uk","Res Artis","Fully funded 11-week residency, flights, accommodation, weekly stipend. Closes 20 September 2026.","2026-09-20","EU","Residency","likely","",recent),
 ("Fondazione Studio Residency 2026","Studio Bocconi","https://example.org/it","Res Artis","Residency in Milan, Italy. Application fee €30. Deadline 5 September 2026.","2026-09-05","EU","Residency","fee-based","€30",recent),
 ("Emerging Painters Prize 2026","A Foundation","https://example.org/prize","e-flux","International award for early-career painters, €10,000. Deadline 12 October 2026.","2026-10-12","Intl","Prize","likely","€10,000",recent),
 ("Open Studio juried exhibition","City Gallery, USA","https://example.org/us","Colossal","Open call, submissions. $25 entry fee. Deadline 30 August 2026. Ohio, USA.","2026-08-30","Intl","Open Call","mixed","$25",older),
]
rows=[]
for t,org,url,srcn,summ,dl,reg,typ,fund,amt,seen in sample:
    from normalize import stable_id
    rows.append(dict(id=stable_id(url,t),title=t,org=org,url=url,source=srcn,summary=summ,
                     deadline=dl,region=reg,type=typ,funded=fund,amount=amt))
# insert with controlled first_seen
import sqlite3
c=sqlite3.connect(store.DB_PATH)
for r,(*_,seen) in zip(rows,sample):
    c.execute("""INSERT OR REPLACE INTO opportunities
      (id,title,org,url,source,summary,deadline,region,type,funded,amount,first_seen,last_seen)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (r["id"],r["title"],r["org"],r["url"],r["source"],r["summary"],r["deadline"],
       r["region"],r["type"],r["funded"],r["amount"],seen,now.isoformat(timespec="seconds")))
c.commit(); c.close()
n=store.export()
print("seeded + exported",n,"rows")
