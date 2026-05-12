# CHIMICA sperimentale

Archivio statico del blog `paoloalbert`, migrato da Libero Blog a GitHub Pages.

Sito pubblico: https://rowandish.github.io/paoloalbert-blog/

## Contenuto

- 433 articoli pubblicati dal 2009 al 2020
- 405 commenti importati come archivio statico
- 840 immagini scaricate localmente nella cartella `img`
- articoli generati nella cartella `src`
- ricerca in home page e archivio completo
- sitemap, canonical e dati strutturati per aiutare l'indicizzazione

## Struttura

```text
.
|-- index.html          # Home page
|-- archive.html        # Archivio completo con ricerca
|-- sitemap.xml         # Sitemap per i motori di ricerca
|-- robots.txt          # Riferimento alla sitemap
|-- assets/             # CSS e JavaScript
|-- data/               # Indice JSON degli articoli
|-- img/                # Immagini salvate localmente
|-- src/                # Articoli statici e redirect legacy
`-- tools/
    `-- build_site.py   # Generatore del sito
```

## Generazione

Il sito è generato da `tools/build_site.py` partendo dai backup locali Libero Blog:

- `liberoblog_000.html`
- `liberoblog_posts_000.csv`

Per rigenerare il sito:

```powershell
python .\tools\build_site.py
```

Il generatore ricostruisce le pagine HTML, riscrive i link interni, usa le immagini locali e aggiorna `sitemap.xml`, `robots.txt`, `data/posts.json`, CSS e JavaScript.

## Pubblicazione

La repo è pubblicata con GitHub Pages dal branch `main`.

Dopo una modifica:

```powershell
git add .
git commit -m "Descrizione modifica"
git push origin main
```

GitHub Pages pubblica automaticamente il sito all'indirizzo:

https://rowandish.github.io/paoloalbert-blog/

## Indicizzazione

Per aiutare Google a riconoscere la nuova posizione degli articoli:

- ogni pagina principale ha un canonical assoluto verso il nuovo URL;
- ogni articolo contiene dati strutturati `BlogPosting`;
- `sitemap.xml` elenca home, archivio e tutti gli articoli;
- `robots.txt` dichiara la sitemap;
- le pagine alias/redirect sono marcate `noindex, follow`.

La sitemap da inviare in Google Search Console è:

```text
https://rowandish.github.io/paoloalbert-blog/sitemap.xml
```
