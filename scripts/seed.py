"""Luo tietokannan alusta ja täyttää sen suomalaisilla ruokakauppatuotteilla.

Käyttö:
    python scripts/seed.py              # polku .env:n DATABASE_PATH:sta
    python scripts/seed.py --db test.db

Skripti on idempotentti: olemassa oleva tietokanta poistetaan ja luodaan uudelleen.

Ruokavaliomerkinnät: G = gluteeniton, L = laktoositon, V = vegaaninen.
Merkinnät ovat konservatiivisia: jos tuote vaihtelee valmistajittain
(esim. makkarat, marinadit), merkintä jätetään pois.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ruokaostos import config, db  # noqa: E402

MERKINNAT = {"G": "gluteeniton", "L": "laktoositon", "V": "vegaaninen"}

# kategoria -> [(nimi, hinta senteissä, yksikkö, merkinnät)]
TUOTTEET: dict[str, list[tuple[str, int, str, str]]] = {
    "Hedelmät ja vihannekset": [
        ("Banaani", 199, "kg", "GLV"),
        ("Omena Granny Smith", 279, "kg", "GLV"),
        ("Appelsiini", 229, "kg", "GLV"),
        ("Mandariini", 299, "kg", "GLV"),
        ("Sitruuna", 349, "kg", "GLV"),
        ("Päärynä", 329, "kg", "GLV"),
        ("Vihreä viinirypäle 500 g", 299, "kpl", "GLV"),
        ("Mansikka 400 g rasia", 399, "kpl", "GLV"),
        ("Mustikka 125 g", 249, "kpl", "GLV"),
        ("Avokado 2 kpl", 299, "kpl", "GLV"),
        ("Kiivi", 349, "kg", "GLV"),
        ("Ananas", 249, "kpl", "GLV"),
        ("Kiinteä peruna 2 kg", 199, "kpl", "GLV"),
        ("Varhaisperuna", 249, "kg", "GLV"),
        ("Porkkana 1 kg", 129, "kpl", "GLV"),
        ("Keltasipuli 1 kg", 149, "kpl", "GLV"),
        ("Punasipuli", 249, "kg", "GLV"),
        ("Valkosipuli 3 kpl", 149, "kpl", "GLV"),
        ("Kurkku", 399, "kg", "GLV"),
        ("Kirsikkatomaatti 250 g", 229, "kpl", "GLV"),
        ("Tomaatti", 449, "kg", "GLV"),
        ("Punainen paprika", 599, "kg", "GLV"),
        ("Jäävuorisalaatti", 199, "kpl", "GLV"),
        ("Rucola 65 g", 199, "kpl", "GLV"),
        ("Parsakaali", 399, "kg", "GLV"),
        ("Kukkakaali", 299, "kpl", "GLV"),
        ("Kesäkurpitsa", 349, "kg", "GLV"),
        ("Lanttu", 199, "kg", "GLV"),
        ("Punajuuri 1 kg", 179, "kpl", "GLV"),
        ("Herkkusieni 250 g", 229, "kpl", "GLV"),
        ("Inkivääri", 699, "kg", "GLV"),
        ("Basilika ruukussa", 199, "kpl", "GLV"),
    ],
    "Maitotuotteet ja kasvijuomat": [
        ("Kevytmaito 1 l", 109, "kpl", "G"),
        ("Rasvaton maito 1 l", 99, "kpl", "G"),
        ("Täysmaito 1 l", 129, "kpl", "G"),
        ("Laktoositon kevytmaitojuoma 1 l", 149, "kpl", "GL"),
        ("Laktoositon rasvaton maitojuoma 1 l", 145, "kpl", "GL"),
        ("Piimä 1 l", 129, "kpl", "G"),
        ("Laktoositon piimä 1 l", 149, "kpl", "GL"),
        ("Kermaviili 200 g", 99, "kpl", "G"),
        ("Laktoositon kermaviili 200 g", 119, "kpl", "GL"),
        ("Ruokakerma 15 % 2 dl", 109, "kpl", "G"),
        ("Laktoositon ruokakerma 15 % 2 dl", 129, "kpl", "GL"),
        ("Vispikerma 2 dl", 159, "kpl", "G"),
        ("Laktoositon vispikerma 2 dl", 179, "kpl", "GL"),
        ("Maustamaton jogurtti 1 kg", 199, "kpl", "G"),
        ("Laktoositon maustamaton jogurtti 1 kg", 239, "kpl", "GL"),
        ("Mansikkajogurtti 150 g", 69, "kpl", "G"),
        ("Turkkilainen jogurtti 10 % 1 kg", 349, "kpl", "G"),
        ("Maustamaton rahka 250 g", 129, "kpl", "G"),
        ("Laktoositon vaniljarahka 200 g", 139, "kpl", "GL"),
        ("Viili 200 g", 79, "kpl", "G"),
        ("Raejuusto 200 g", 159, "kpl", "G"),
        ("Laktoositon raejuusto 200 g", 179, "kpl", "GL"),
        ("Normaalisuolainen voi 500 g", 549, "kpl", "G"),
        ("Vegaaninen kasvirasvalevite 400 g", 299, "kpl", "GLV"),
        ("Kaurajuoma 1 l", 179, "kpl", "V"),
        ("Soijajuoma 1 l", 169, "kpl", "GV"),
        ("Mantelijuoma 1 l", 229, "kpl", "GV"),
        ("Maustamaton kauragurtti 400 g", 199, "kpl", "V"),
        ("Kaurakerma ruoanvalmistukseen 2 dl", 119, "kpl", "V"),
    ],
    "Juustot": [
        ("Edam viipale 300 g", 399, "kpl", "GL"),
        ("Emmental 400 g", 549, "kpl", "GL"),
        ("Kermajuusto viipale 300 g", 429, "kpl", "GL"),
        ("Cheddar viipale 150 g", 299, "kpl", "GL"),
        ("Parmesaaniraaste 80 g", 349, "kpl", "GL"),
        ("Sinihomejuusto 170 g", 359, "kpl", "GL"),
        ("Fetajuusto 200 g", 299, "kpl", "G"),
        ("Mozzarella 125 g", 179, "kpl", "G"),
        ("Halloumi 200 g", 399, "kpl", "G"),
        ("Leipäjuusto 220 g", 389, "kpl", "G"),
        ("Maustamaton tuorejuusto 200 g", 229, "kpl", "G"),
        ("Laktoositon tuorejuusto 200 g", 259, "kpl", "GL"),
        ("Vegaaninen juustoraaste 200 g", 349, "kpl", "GLV"),
    ],
    "Liha": [
        ("Naudan jauheliha 10 % 400 g", 549, "kpl", "GL"),
        ("Sika-nauta-jauheliha 400 g", 399, "kpl", "GL"),
        ("Broilerin rintafilee 600 g", 899, "kpl", "GL"),
        ("Maustamaton broilerin fileesuikale 400 g", 599, "kpl", "GL"),
        ("Marinoitu broilerin fileesuikale 400 g", 649, "kpl", ""),
        ("Kokonainen broileri", 549, "kg", "GL"),
        ("Porsaan ulkofilee", 1199, "kg", "GL"),
        ("Porsaan kasslerpaisti", 899, "kg", "GL"),
        ("Naudan sisäfilee", 4999, "kg", "GL"),
        ("Naudan entrecôte", 3499, "kg", "GL"),
        ("Lenkkimakkara 400 g", 299, "kpl", ""),
        ("Nakit 300 g", 249, "kpl", ""),
        ("Grillimakkara 400 g", 349, "kpl", ""),
        ("Pekoni 140 g", 199, "kpl", "GL"),
        ("Keittokinkkuleike 300 g", 399, "kpl", "GL"),
        ("Kalkkunaleike 150 g", 249, "kpl", "GL"),
        ("Meetvursti 150 g", 249, "kpl", "GL"),
        ("Lihapullat 400 g", 399, "kpl", ""),
    ],
    "Kala ja äyriäiset": [
        ("Lohifilee", 1999, "kg", "GL"),
        ("Kylmäsavulohi viipale 200 g", 699, "kpl", "GL"),
        ("Graavilohi viipale 200 g", 699, "kpl", "GL"),
        ("Lämminsavukirjolohi 300 g", 799, "kpl", "GL"),
        ("Silakkafilee", 899, "kg", "GL"),
        ("Tuore muikku", 1290, "kg", "GL"),
        ("Kuoritut katkaravut 200 g", 549, "kpl", "GL"),
        ("Sinappisilli 250 g", 299, "kpl", ""),
    ],
    "Leivät ja leivonnaiset": [
        ("Ruispalat 6 kpl", 199, "kpl", "LV"),
        ("Reikäleipä 500 g", 249, "kpl", "LV"),
        ("Saaristolaisleipä 700 g", 349, "kpl", ""),
        ("Vehnäpaahtoleipä 500 g", 169, "kpl", ""),
        ("Kauraleipä 500 g", 249, "kpl", ""),
        ("Ciabatta 2 kpl", 199, "kpl", "LV"),
        ("Patonki", 179, "kpl", "LV"),
        ("Vehnäsämpylä 6 kpl", 249, "kpl", ""),
        ("Ruisnäkkileipä 230 g", 179, "kpl", "LV"),
        ("Hapankorppu 200 g", 189, "kpl", "LV"),
        ("Vehnätortilla 8 kpl", 229, "kpl", "LV"),
        ("Karjalanpiirakka 10 kpl", 349, "kpl", ""),
        ("Korvapuusti 4 kpl", 349, "kpl", ""),
        ("Voicroissant 4 kpl", 299, "kpl", ""),
        ("Gluteeniton vaalea leipä 300 g", 449, "kpl", "GL"),
        ("Gluteeniton tumma leipä 300 g", 449, "kpl", "GL"),
    ],
    "Kuivatuotteet": [
        ("Spagetti 500 g", 129, "kpl", "LV"),
        ("Makaroni 400 g", 109, "kpl", "LV"),
        ("Täysjyväpenne 500 g", 159, "kpl", "LV"),
        ("Gluteeniton spagetti 400 g", 289, "kpl", "GLV"),
        ("Pitkäjyväinen riisi 1 kg", 229, "kpl", "GLV"),
        ("Jasmiiniriisi 1 kg", 299, "kpl", "GLV"),
        ("Basmatiriisi 1 kg", 349, "kpl", "GLV"),
        ("Puuroriisi 1 kg", 199, "kpl", "GLV"),
        ("Kvinoa 400 g", 349, "kpl", "GLV"),
        ("Bulgur 500 g", 199, "kpl", "LV"),
        ("Vehnäjauho 2 kg", 149, "kpl", "LV"),
        ("Ruisjauho 1 kg", 159, "kpl", "LV"),
        ("Gluteeniton jauhoseos 1 kg", 449, "kpl", "GLV"),
        ("Perunajauho 900 g", 249, "kpl", "GLV"),
        ("Taloussokeri 1 kg", 129, "kpl", "GLV"),
        ("Fariinisokeri 500 g", 179, "kpl", "GLV"),
        ("Leivinjauhe 100 g", 99, "kpl", "GLV"),
        ("Kuivahiiva 3 x 11 g", 89, "kpl", "GLV"),
        ("Punaiset linssit 500 g", 249, "kpl", "GLV"),
        ("Kuivatut kikherneet 500 g", 229, "kpl", "GLV"),
        ("Tacokuoret 12 kpl", 199, "kpl", "GLV"),
    ],
    "Säilykkeet": [
        ("Tomaattimurska 400 g", 99, "kpl", "GLV"),
        ("Kuoritut tomaatit 400 g", 109, "kpl", "GLV"),
        ("Tomaattipyree 200 g", 129, "kpl", "GLV"),
        ("Kookosmaito 400 ml", 179, "kpl", "GLV"),
        ("Kikherneet 400 g", 99, "kpl", "GLV"),
        ("Kidneypavut 400 g", 99, "kpl", "GLV"),
        ("Mustapavut 400 g", 109, "kpl", "GLV"),
        ("Maissi 340 g", 139, "kpl", "GLV"),
        ("Tonnikala vedessä 185 g", 199, "kpl", "GL"),
        ("Ananasrenkaat 565 g", 179, "kpl", "GLV"),
        ("Etikkakurkut 670 g", 249, "kpl", "GLV"),
        ("Kivettömät vihreät oliivit 350 g", 229, "kpl", "GLV"),
        ("Hernekeitto 435 g", 199, "kpl", "GL"),
    ],
    "Pakasteet": [
        ("Pakasteherneet 500 g", 179, "kpl", "GLV"),
        ("Wokkivihannekset 500 g", 229, "kpl", "GLV"),
        ("Pakastemustikat 1 kg", 549, "kpl", "GLV"),
        ("Pakastemansikat 1 kg", 499, "kpl", "GLV"),
        ("Ranskalaiset perunat 1 kg", 249, "kpl", "GLV"),
        ("Kalapuikot 450 g", 399, "kpl", "L"),
        ("Seitifilee 400 g", 499, "kpl", "GL"),
        ("Pakastepizza margherita 350 g", 299, "kpl", ""),
        ("Vaniljajäätelö 1 l", 449, "kpl", "G"),
        ("Laktoositon vaniljajäätelö 1 l", 499, "kpl", "GL"),
        ("Kauravaniljajäätelö 500 ml", 499, "kpl", "V"),
    ],
    "Juomat": [
        ("Appelsiinitäysmehu 1 l", 229, "kpl", "GLV"),
        ("Omenatäysmehu 1 l", 219, "kpl", "GLV"),
        ("Mustaherukkamehutiiviste 1 l", 349, "kpl", "GLV"),
        ("Kivennäisvesi 1,5 l", 99, "kpl", "GLV"),
        ("Cola 1,5 l", 299, "kpl", "GLV"),
        ("Sokeriton cola 1,5 l", 299, "kpl", "GLV"),
        ("Appelsiinilimonadi 1,5 l", 249, "kpl", "GLV"),
        ("Energiajuoma 0,5 l", 199, "kpl", "GLV"),
        ("Alkoholiton olut 0,33 l", 149, "kpl", "LV"),
        ("Suodatinjauhettu kahvi 500 g", 699, "kpl", "GLV"),
        ("Tummapaahtokahvi 500 g", 749, "kpl", "GLV"),
        ("Kahvipavut 1 kg", 1499, "kpl", "GLV"),
        ("Musta tee 25 pss", 249, "kpl", "GLV"),
        ("Vihreä tee 20 pss", 229, "kpl", "GLV"),
        ("Kaakaojauhe 200 g", 349, "kpl", "GLV"),
        ("Kaakaojuomajauhe 400 g", 399, "kpl", "G"),
    ],
    "Makeiset ja naposteltavat": [
        ("Maitosuklaa 200 g", 349, "kpl", "G"),
        ("Maidoton tumma suklaa 70 % 100 g", 249, "kpl", "GLV"),
        ("Salmiakkikarkit 220 g", 249, "kpl", "LV"),
        ("Lakritsi 300 g", 249, "kpl", "LV"),
        ("Hedelmäkarkit 200 g", 229, "kpl", "GL"),
        ("Suolatut perunalastut 275 g", 299, "kpl", "GLV"),
        ("Sour cream -perunalastut 275 g", 299, "kpl", "G"),
        ("Mikropopcorn 3 x 90 g", 249, "kpl", "G"),
        ("Suolapähkinät 300 g", 349, "kpl", "GLV"),
        ("Cashewpähkinät 200 g", 399, "kpl", "GLV"),
        ("Mantelit 200 g", 349, "kpl", "GLV"),
        ("Rusinat 250 g", 199, "kpl", "GLV"),
        ("Riisikakut 100 g", 129, "kpl", "GLV"),
        ("Suklaakeksit 300 g", 249, "kpl", ""),
        ("Digestive-keksit 400 g", 229, "kpl", ""),
    ],
    "Mausteet, öljyt ja kastikkeet": [
        ("Merisuola 500 g", 129, "kpl", "GLV"),
        ("Rouhittu mustapippuri 50 g", 249, "kpl", "GLV"),
        ("Paprikajauhe 50 g", 199, "kpl", "GLV"),
        ("Kaneli 50 g", 199, "kpl", "GLV"),
        ("Currymauste 40 g", 199, "kpl", "GLV"),
        ("Ketsuppi 500 g", 229, "kpl", "GLV"),
        ("Sinappi 275 g", 199, "kpl", "LV"),
        ("Majoneesi 250 g", 299, "kpl", "GL"),
        ("Soijakastike 150 ml", 249, "kpl", "LV"),
        ("Gluteeniton tamari-soijakastike 150 ml", 349, "kpl", "GLV"),
        ("Extra virgin -oliiviöljy 500 ml", 699, "kpl", "GLV"),
        ("Rypsiöljy 1 l", 349, "kpl", "GLV"),
        ("Väkiviinaetikka 10 % 0,5 l", 99, "kpl", "GLV"),
        ("Hunaja 450 g", 549, "kpl", "GL"),
        ("Kasvisliemikuutio 6 kpl", 149, "kpl", "LV"),
        ("Pesto genovese 190 g", 299, "kpl", "G"),
    ],
    "Kasviproteiinit": [
        ("Maustamaton tofu 270 g", 249, "kpl", "GLV"),
        ("Härkäpapurouhe 250 g", 299, "kpl", "GLV"),
        ("Soijarouhe 200 g", 249, "kpl", "GLV"),
        ("Tempeh 200 g", 399, "kpl", "GLV"),
        ("Nyhtökaura 250 g", 399, "kpl", "LV"),
        ("Kasvispihvit 4 kpl", 349, "kpl", "LV"),
        ("Falafel 300 g", 349, "kpl", "LV"),
        ("Seitan 250 g", 399, "kpl", "LV"),
        ("Kasvisnakit 300 g", 349, "kpl", "LV"),
    ],
    "Aamupala": [
        ("Kaurahiutaleet 1 kg", 159, "kpl", "LV"),
        ("Gluteenittomat kaurahiutaleet 500 g", 299, "kpl", "GLV"),
        ("Mysli 750 g", 349, "kpl", "LV"),
        ("Maissihiutaleet 375 g", 279, "kpl", "LV"),
        ("Hunajagranola 500 g", 399, "kpl", "L"),
        ("Kananmunat M 10 kpl", 299, "kpl", "GL"),
        ("Vapaan kanan munat 10 kpl", 349, "kpl", "GL"),
        ("Mansikkahillo 400 g", 249, "kpl", "GLV"),
        ("Appelsiinimarmeladi 400 g", 249, "kpl", "GLV"),
        ("Maapähkinävoi 350 g", 349, "kpl", "GLV"),
    ],
    "Valmisruoat": [
        ("Maksalaatikko 400 g", 279, "kpl", "G"),
        ("Makaronilaatikko 400 g", 349, "kpl", ""),
        ("Lasagne 400 g", 399, "kpl", ""),
        ("Karjalanpaisti 300 g", 449, "kpl", "GL"),
        ("Lihakeitto 300 g", 299, "kpl", "GL"),
        ("Lohikeitto 300 g", 399, "kpl", "G"),
        ("Kasviscurry 350 g", 399, "kpl", "GLV"),
        ("Perunamuusi 400 g", 249, "kpl", "G"),
        ("Pinaattiohukaiset 200 g", 249, "kpl", ""),
        ("Lihapiirakka 2 kpl", 249, "kpl", ""),
    ],
}


def seed(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)

    with db.connect(db_path) as conn:
        db.init_schema(conn)
        ruokavalio_id = {
            r["koodi"]: r["id"] for r in conn.execute("SELECT id, koodi FROM ruokavaliot")
        }

        for kategoria, tuotteet in TUOTTEET.items():
            kategoria_id = conn.execute(
                "INSERT INTO kategoriat (nimi) VALUES (?)", (kategoria,)
            ).lastrowid
            for nimi, hinta_snt, yksikko, merkinnat in tuotteet:
                # Vegaaninen tuote on aina myös laktoositon; johdetaan, ettei data ole ristiriitainen.
                if "V" in merkinnat:
                    merkinnat += "L"
                tuote_id = conn.execute(
                    "INSERT INTO tuotteet (nimi, kategoria_id, hinta_snt, yksikko) VALUES (?, ?, ?, ?)",
                    (nimi, kategoria_id, hinta_snt, yksikko),
                ).lastrowid
                conn.executemany(
                    "INSERT OR IGNORE INTO tuote_ruokavalio (tuote_id, ruokavalio_id) VALUES (?, ?)",
                    [(tuote_id, ruokavalio_id[MERKINNAT[m]]) for m in set(merkinnat)],
                )

        tuotteita = conn.execute("SELECT COUNT(*) FROM tuotteet").fetchone()[0]
        print(f"Luotu {db_path}: {tuotteita} tuotetta, {len(TUOTTEET)} kategoriaa")
        for r in conn.execute(
            """SELECT r.nimi, COUNT(tr.tuote_id) AS n FROM ruokavaliot r
               LEFT JOIN tuote_ruokavalio tr ON tr.ruokavalio_id = r.id
               GROUP BY r.id ORDER BY r.id"""
        ):
            print(f"  {r['nimi']}: {r['n']}")
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=config.DATABASE_PATH, help="tietokannan polku")
    seed(parser.parse_args().db)


if __name__ == "__main__":
    main()
