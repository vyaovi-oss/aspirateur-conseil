#!/usr/bin/env python3
"""
Générateur du site statique aspirateur-conseil.com

Commande : python3 build.py
Résultat : le dossier _site/ (c'est lui que Cloudflare Pages met en ligne)
"""

import html
import json
import math
import re
import shutil
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote_plus

import markdown
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

RACINE = Path(__file__).parent
SORTIE = RACINE / "_site"
ARTICLES_DIR = RACINE / "content" / "articles"
PAGES_DIR = RACINE / "content" / "pages"
STATIC_DIR = RACINE / "static"

MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre"]


# ---------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------

def charger_config():
    with open(RACINE / "config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    config["url"] = config["url"].rstrip("/")
    config["categories_par_slug"] = {c["slug"]: c for c in config["categories"]}
    return config


def lire_fichier_md(chemin):
    """Sépare le frontmatter YAML (entre les ---) du texte Markdown."""
    texte = chemin.read_text(encoding="utf-8")
    if texte.startswith("﻿"):
        texte = texte[1:]
    meta, corps = {}, texte
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", texte, re.DOTALL)
    if m:
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError as e:
            raise SystemExit(
                f"\n❌ Erreur YAML dans {chemin.name} :\n{e}\n"
                "Astuce : mets des guillemets autour des textes qui contiennent « : ».\n"
            )
        corps = m.group(2)
    return meta, corps


def en_date(valeur):
    if isinstance(valeur, datetime):
        return valeur.date()
    if isinstance(valeur, date):
        return valeur
    if isinstance(valeur, str) and valeur.strip():
        try:
            return datetime.strptime(valeur.strip()[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    return date.today()


def date_fr(d):
    return f"{d.day} {MOIS[d.month - 1]} {d.year}"


def slugifier(texte):
    import unicodedata
    texte = unicodedata.normalize("NFKD", str(texte)).encode("ascii", "ignore").decode()
    texte = re.sub(r"[^a-zA-Z0-9]+", "-", texte).strip("-").lower()
    return texte or "article"


# ---------------------------------------------------------------------------
# Liens Amazon automatiques
# ---------------------------------------------------------------------------

def lien_amazon(config, nom="", asin=""):
    """Lien Amazon avec le tag affilié : page produit si ASIN, sinon recherche."""
    domaine = config["amazon_domaine"]
    tag = config["amazon_tag"]
    asin = (asin or "").strip()
    if asin:
        return f"https://{domaine}/dp/{asin}?tag={tag}"
    return f"https://{domaine}/s?k={quote_plus(str(nom).strip())}&tag={tag}"


def traiter_liens_amazon(html_texte, config):
    """
    1) Remplace les raccourcis [[amazon:Nom du produit]] par un lien affilié.
    2) Ajoute le tag et rel="sponsored nofollow" à tous les liens vers Amazon.
    """
    def raccourci(m):
        nom = html.unescape(m.group(1).strip())
        url = lien_amazon(config, nom=nom)
        return (f'<a href="{html.escape(url)}" class="lien-amazon" '
                f'rel="sponsored nofollow noopener" target="_blank">{html.escape(nom)}</a>')

    html_texte = re.sub(r"\[\[amazon:([^\]]+)\]\]", raccourci, html_texte)

    def corriger_lien(m):
        balise = m.group(0)
        href_m = re.search(r'href="([^"]+)"', balise)
        if not href_m:
            return balise
        href = html.unescape(href_m.group(1))
        if not re.search(r"(amazon\.fr|amzn\.to|amzn\.eu)", href):
            return balise
        if "amazon.fr" in href and "tag=" not in href:
            href += ("&" if "?" in href else "?") + f"tag={config['amazon_tag']}"
        balise = balise.replace(href_m.group(0), f'href="{html.escape(href)}"')
        balise = re.sub(r'\s(rel|target)="[^"]*"', "", balise)
        balise = balise[:-1] + ' rel="sponsored nofollow noopener" target="_blank">'
        return balise

    return re.sub(r"<a\s[^>]*>", corriger_lien, html_texte)


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def md_vers_html(texte, config):
    md = markdown.Markdown(
        extensions=["extra", "toc", "sane_lists"],
        extension_configs={"toc": {"slugify": lambda v, s: slugifier(v), "toc_depth": "2"}},
    )
    contenu = md.convert(texte or "")
    contenu = traiter_liens_amazon(contenu, config)
    # Tableaux défilables sur mobile
    contenu = contenu.replace("<table>", '<div class="tableau"><table>').replace("</table>", "</table></div>")
    sommaire = [{"id": t["id"], "titre": t["name"]} for t in md.toc_tokens]
    return contenu, sommaire


def texte_simple(html_texte):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html_texte)).strip()


# ---------------------------------------------------------------------------
# Chargement des contenus
# ---------------------------------------------------------------------------

def charger_articles(config):
    articles = []
    if not ARTICLES_DIR.exists():
        return articles
    for chemin in sorted(ARTICLES_DIR.glob("*.md")):
        meta, corps = lire_fichier_md(chemin)
        if meta.get("brouillon"):
            print(f"  ⏸  Brouillon ignoré : {chemin.name}")
            continue
        slug = slugifier(meta.get("slug") or chemin.stem)
        contenu, sommaire = md_vers_html(corps, config)

        produits = []
        for p in meta.get("produits") or []:
            if not isinstance(p, dict) or not p.get("nom"):
                continue
            p = dict(p)
            p["lien"] = lien_amazon(config, nom=p.get("nom"), asin=p.get("asin"))
            p["points_forts"] = [x for x in (p.get("points_forts") or []) if x]
            p["points_faibles"] = [x for x in (p.get("points_faibles") or []) if x]
            produits.append(p)

        faq = []
        for q in meta.get("faq") or []:
            if isinstance(q, dict) and q.get("question") and q.get("reponse"):
                rep_html, _ = md_vers_html(str(q["reponse"]), config)
                faq.append({"question": q["question"], "reponse": rep_html,
                            "reponse_texte": texte_simple(rep_html)})

        cat_slug = meta.get("categorie") or ""
        if cat_slug and cat_slug not in config["categories_par_slug"]:
            print(f"  ⚠️  Catégorie inconnue « {cat_slug} » dans {chemin.name}")

        # [[produits]] dans le texte = endroit où afficher les fiches produits
        morceaux = re.split(r"(?:<p>)?\s*\[\[produits\]\]\s*(?:</p>)?", contenu, maxsplit=1)
        contenu_avant, contenu_apres = (morceaux[0], morceaux[1]) if len(morceaux) == 2 else ("", contenu)

        d = en_date(meta.get("date"))
        maj = en_date(meta.get("mise_a_jour")) if meta.get("mise_a_jour") else d
        mots = len(texte_simple(contenu).split())

        articles.append({
            "titre": meta.get("titre") or slug,
            "titre_seo": meta.get("titre_seo") or meta.get("titre") or slug,
            "slug": slug,
            "url": f"/{slug}/",
            "description": meta.get("description") or "",
            "categorie": config["categories_par_slug"].get(cat_slug),
            "date": d,
            "date_fr": date_fr(d),
            "mise_a_jour": maj,
            "mise_a_jour_fr": date_fr(maj),
            "image_url": meta.get("image_url") or "",
            "image_credit": meta.get("image_credit") or "",
            "produits": produits,
            "faq": faq,
            "contenu": contenu,
            "contenu_avant": contenu_avant,
            "contenu_apres": contenu_apres,
            "sommaire": sommaire,
            "temps_lecture": max(1, math.ceil(mots / 220)),
        })
    articles.sort(key=lambda a: (a["date"], a["titre"]), reverse=True)
    return articles


def charger_pages(config):
    pages = []
    if not PAGES_DIR.exists():
        return pages
    for chemin in sorted(PAGES_DIR.glob("*.md")):
        meta, corps = lire_fichier_md(chemin)
        slug = slugifier(meta.get("slug") or chemin.stem)
        contenu, _ = md_vers_html(corps, config)
        pages.append({
            "titre": meta.get("titre") or slug,
            "titre_seo": meta.get("titre_seo") or meta.get("titre") or slug,
            "slug": slug,
            "url": f"/{slug}/",
            "description": meta.get("description") or "",
            "contenu": contenu,
            "noindex": bool(meta.get("noindex")),
        })
    return pages


# ---------------------------------------------------------------------------
# Données structurées (JSON-LD) pour Google
# ---------------------------------------------------------------------------

def jsonld_article(a, config):
    url = config["url"] + a["url"]
    blocs = [{
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": a["titre"],
        "description": a["description"],
        "datePublished": a["date"].isoformat(),
        "dateModified": a["mise_a_jour"].isoformat(),
        "author": {"@type": "Person", "name": config["auteur"]},
        "publisher": {"@type": "Organization", "name": config["nom_site"]},
        "mainEntityOfPage": url,
        **({"image": a["image_url"]} if a["image_url"] else {}),
    }]
    fil = [{"@type": "ListItem", "position": 1, "name": "Accueil", "item": config["url"] + "/"}]
    if a["categorie"]:
        fil.append({"@type": "ListItem", "position": 2, "name": a["categorie"]["nom"],
                    "item": f"{config['url']}/categorie/{a['categorie']['slug']}/"})
    fil.append({"@type": "ListItem", "position": len(fil) + 1, "name": a["titre"], "item": url})
    blocs.append({"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": fil})
    if a["faq"]:
        blocs.append({
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [{"@type": "Question", "name": q["question"],
                            "acceptedAnswer": {"@type": "Answer", "text": q["reponse_texte"]}}
                           for q in a["faq"]],
        })
    return [json.dumps(b, ensure_ascii=False) for b in blocs]


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def ecrire(chemin_relatif, contenu):
    chemin = SORTIE / chemin_relatif
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(contenu, encoding="utf-8")


def main():
    print("🔧 Construction du site…")
    config = charger_config()

    if SORTIE.exists():
        shutil.rmtree(SORTIE)
    SORTIE.mkdir()
    if STATIC_DIR.exists():
        shutil.copytree(STATIC_DIR, SORTIE, dirs_exist_ok=True)

    env = Environment(loader=FileSystemLoader(RACINE / "templates"),
                      autoescape=select_autoescape(["html"]))
    env.globals.update(site=config, annee=date.today().year)

    articles = charger_articles(config)
    pages = charger_pages(config)

    # Nombre d'articles par catégorie (pour le menu et l'accueil)
    for c in config["categories"]:
        c["articles"] = [a for a in articles if a["categorie"] and a["categorie"]["slug"] == c["slug"]]

    # Accueil
    ecrire("index.html", env.get_template("index.html").render(
        articles=articles, page_url="/"))

    # Articles
    for a in articles:
        similaires = [x for x in articles if x["slug"] != a["slug"]
                      and a["categorie"] and x["categorie"] == a["categorie"]][:3]
        if len(similaires) < 3:
            similaires += [x for x in articles if x["slug"] != a["slug"] and x not in similaires][:3 - len(similaires)]
        ecrire(f"{a['slug']}/index.html", env.get_template("article.html").render(
            a=a, similaires=similaires, jsonld=jsonld_article(a, config), page_url=a["url"]))

    # Catégories
    for c in config["categories"]:
        ecrire(f"categorie/{c['slug']}/index.html", env.get_template("categorie.html").render(
            c=c, articles=c["articles"], page_url=f"/categorie/{c['slug']}/"))

    # Pages (mentions légales, contact…)
    for p in pages:
        ecrire(f"{p['slug']}/index.html", env.get_template("page.html").render(
            p=p, page_url=p["url"]))

    # Page 404
    ecrire("404.html", env.get_template("404.html").render(
        articles=articles[:6], page_url="/404.html"))

    # Plan du site (sitemap.xml)
    urls = [(config["url"] + "/", articles[0]["mise_a_jour"] if articles else date.today())]
    urls += [(config["url"] + a["url"], a["mise_a_jour"]) for a in articles]
    urls += [(f"{config['url']}/categorie/{c['slug']}/", None) for c in config["categories"] if c["articles"]]
    urls += [(config["url"] + p["url"], None) for p in pages if not p["noindex"]]
    lignes = ['<?xml version="1.0" encoding="UTF-8"?>',
              '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, lastmod in urls:
        lignes.append(f"  <url><loc>{html.escape(loc)}</loc>"
                      + (f"<lastmod>{lastmod.isoformat()}</lastmod>" if lastmod else "")
                      + "</url>")
    lignes.append("</urlset>")
    ecrire("sitemap.xml", "\n".join(lignes) + "\n")

    # robots.txt
    ecrire("robots.txt", f"User-agent: *\nAllow: /\n\nSitemap: {config['url']}/sitemap.xml\n")

    print(f"✅ Terminé : {len(articles)} article(s), {len(pages)} page(s), "
          f"{len(config['categories'])} catégorie(s) → dossier _site/")


if __name__ == "__main__":
    main()
