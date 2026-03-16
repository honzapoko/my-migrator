import streamlit as st
import re
import zipfile
import io
import html
from datetime import datetime

st.set_page_config(
    page_title="WP → Static Migrator",
    page_icon="🛡️",
    layout="centered"
)

# ─── CSS for the Streamlit dashboard itself ────────────────────────────────
st.markdown("""
<style>
    .stApp { background: #f0f4f8; }
    .block-container { max-width: 780px; padding-top: 2rem; }
    h1 { color: #1a202c; }
    .stat-box { background: white; border-radius: 8px; padding: 16px 20px;
                border-left: 4px solid #3182ce; margin: 8px 0; }
    .stat-box b { font-size: 1.4rem; color: #3182ce; }
    .warn { background: #fff8e1; border-left: 4px solid #f59e0b;
            border-radius: 6px; padding: 12px 16px; margin: 10px 0; }
</style>
""", unsafe_allow_html=True)

st.title("🛡️ WordPress → Static Site Migrator")
st.markdown("Upload a WordPress SQL database. The tool cleans malware, recovers content, and generates a ready-to-deploy static website.")

# ══════════════════════════════════════════════════════════════════
# PARSING & CLEANING
# ══════════════════════════════════════════════════════════════════

def unescape_sql(s):
    if not s:
        return ""
    return (s.replace("\\'", "'")
             .replace('\\"', '"')
             .replace('\\\\', '\\')
             .replace('\\r\\n', '\n')
             .replace('\\n', '\n')
             .replace('\\r', '\n'))

def clean_malware(content):
    if not content:
        return ""
    # Remove ALL <script> tags — no legit static content needs inline JS
    content = re.sub(r'<script[\s\S]*?</script>', '', content, flags=re.IGNORECASE)
    # Remove meta refresh redirects
    content = re.sub(r'<meta[^>]+http-equiv[^>]+>', '', content, flags=re.IGNORECASE)
    # Remove any remaining urshort links
    content = re.sub(r'[^\n]*urshort\.com[^\n]*', '', content)
    # Remove WordPress block editor comments
    content = re.sub(r'<!--\s*/?wp:[^>]*-->', '', content)
    # Fix image URLs to be relative
    content = re.sub(r'https?://[^/\s"\']+/wp-content/', '/wp-content/', content)
    # Clean up excess blank lines
    content = re.sub(r'\n{3,}', '\n\n', content).strip()
    return content

def parse_sql_rows(block):
    """State-machine SQL row parser that handles escaped quotes inside strings."""
    vals_start = block.find(') VALUES\n')
    if vals_start == -1:
        return []
    data = block[vals_start + 9:]
    rows = []
    i = 0
    n = len(data)

    while i < n:
        while i < n and data[i] != '(':
            i += 1
        if i >= n:
            break
        i += 1
        fields = []
        buf = []

        while i < n:
            c = data[i]
            if c == "'":
                i += 1
                while i < n:
                    ch = data[i]
                    if ch == '\\':
                        buf.append(ch); i += 1
                        if i < n:
                            buf.append(data[i]); i += 1
                    elif ch == "'":
                        i += 1; break
                    else:
                        buf.append(ch); i += 1
                fields.append(''.join(buf)); buf = []
                while i < n and data[i] == ' ':
                    i += 1
                if i < n and data[i] == ',':
                    i += 1
                elif i < n and data[i] == ')':
                    i += 1; break
            elif c in '0123456789-':
                while i < n and data[i] not in (',', ')'):
                    buf.append(data[i]); i += 1
                fields.append(''.join(buf).strip()); buf = []
                if i < n and data[i] == ',':
                    i += 1
                elif i < n and data[i] == ')':
                    i += 1; break
            elif data[i:i+4] == 'NULL':
                fields.append(''); i += 4
                while i < n and data[i] == ' ':
                    i += 1
                if i < n and data[i] == ',':
                    i += 1
                elif i < n and data[i] == ')':
                    i += 1; break
            else:
                i += 1

        if len(fields) >= 22:
            rows.append(fields)
    return rows

def parse_sql(sql_text):
    """Full parse of WordPress SQL dump. Returns posts, categories, post→category map."""

    # ── Users ──────────────────────────────────────────────────────
    users = {}
    um = re.search(r"INSERT INTO `wp_users`[^;]+;", sql_text, re.DOTALL)
    if um:
        for m in re.finditer(r"\((\d+),\s*'[^']*',\s*'[^']*',\s*'[^']*',\s*'[^']*',\s*'[^']*',\s*'[^']*',\s*'[^']*',\s*\d+,\s*'([^']*)'", um.group(0)):
            uid = int(m.group(1))
            name = m.group(2)
            if name not in ('dev0', 'root', 'aios'):  # skip attacker accounts
                users[uid] = name

    # ── Terms (categories) ─────────────────────────────────────────
    terms = {}
    tm = re.search(r"INSERT INTO `wp_terms` \([^)]+\) VALUES\n([\s\S]+?);\n", sql_text)
    if tm:
        for m in re.finditer(r"\((\d+),\s*'((?:[^'\\]|\\.)*)',\s*'((?:[^'\\]|\\.)*)'", tm.group(1)):
            terms[int(m.group(1))] = {'name': m.group(2), 'slug': m.group(3)}

    taxonomies = {}
    txm = re.search(r"INSERT INTO `wp_term_taxonomy` \([^)]+\) VALUES\n([\s\S]+?);\n", sql_text)
    if txm:
        for m in re.finditer(r"\((\d+),\s*(\d+),\s*'([^']*)'", txm.group(1)):
            tt_id, term_id, taxonomy = int(m.group(1)), int(m.group(2)), m.group(3)
            if taxonomy == 'category' and term_id in terms:
                taxonomies[tt_id] = terms[term_id]

    post_cats = {}  # post_id (int) -> [category names]
    rm = re.search(r"INSERT INTO `wp_term_relationships` \([^)]+\) VALUES\n([\s\S]+?);\n", sql_text)
    if rm:
        for m in re.finditer(r"\((\d+),\s*(\d+),", rm.group(1)):
            pid, tt_id = int(m.group(1)), int(m.group(2))
            if tt_id in taxonomies:
                post_cats.setdefault(pid, []).append(taxonomies[tt_id]['name'])

    # ── Posts ──────────────────────────────────────────────────────
    # Columns: ID(0) author(1) date(2) date_gmt(3) content(4) title(5)
    #          excerpt(6) status(7) comment_status(8) ping_status(9)
    #          password(10) slug(11) to_ping(12) pinged(13) modified(14)
    #          modified_gmt(15) content_filtered(16) parent(17) guid(18)
    #          menu_order(19) post_type(20) mime_type(21) comment_count(22)

    insert_blocks = re.findall(r"INSERT INTO `wp_posts` \([^)]+\) VALUES\n[\s\S]+?;\n", sql_text)
    posts = []
    for block in insert_blocks:
        for r in parse_sql_rows(block):
            if len(r) < 22:
                continue
            post_type = r[20]
            status = r[7]
            if post_type not in ('post', 'page') or status != 'publish':
                continue
            pid = int(r[0])
            author_id = int(r[1]) if r[1] else 1
            content_clean = clean_malware(unescape_sql(r[4]))
            title = html.unescape(unescape_sql(r[5]))
            posts.append({
                'id': pid,
                'author': users.get(author_id, 'Redakce'),
                'date': r[2],
                'content': content_clean,
                'title': title,
                'excerpt': unescape_sql(r[6]),
                'slug': r[11] or f"post-{pid}",
                'post_type': post_type,
                'categories': post_cats.get(pid, []),
            })

    # Sort by date descending
    posts.sort(key=lambda p: p['date'], reverse=True)
    return posts, list(taxonomies.values())


# ══════════════════════════════════════════════════════════════════
# HTML GENERATION
# ══════════════════════════════════════════════════════════════════

SITE_CSS = """
/* ── Reset & Variables ─────────────────────────── */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --primary:   #1a365d;
  --accent:    #2b6cb0;
  --highlight: #ebf8ff;
  --text:      #2d3748;
  --muted:     #718096;
  --bg:        #f7fafc;
  --card:      #ffffff;
  --border:    #e2e8f0;
  --radius:    8px;
  --shadow:    0 2px 12px rgba(0,0,0,0.07);
  --font-body: 'Georgia', 'Times New Roman', serif;
  --font-ui:   -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}

/* ── Base ──────────────────────────────────────── */
body {
  font-family: var(--font-body);
  background: var(--bg);
  color: var(--text);
  line-height: 1.75;
  font-size: 17px;
}

/* ── Header ────────────────────────────────────── */
.site-header {
  background: var(--primary);
  color: white;
  position: sticky; top: 0; z-index: 100;
  box-shadow: 0 2px 8px rgba(0,0,0,0.2);
}
.header-inner {
  max-width: 1100px; margin: 0 auto;
  padding: 0 24px;
  display: flex; align-items: center;
  justify-content: space-between;
  height: 64px;
}
.site-logo {
  font-family: var(--font-ui);
  font-size: 1.35rem; font-weight: 700;
  color: white; text-decoration: none;
  letter-spacing: -0.3px;
}
.site-logo span { color: #90cdf4; }

/* ── Navigation ────────────────────────────────── */
.main-nav { display: flex; gap: 4px; align-items: center; }
.main-nav a {
  font-family: var(--font-ui);
  font-size: 0.85rem; font-weight: 500;
  color: rgba(255,255,255,0.85);
  text-decoration: none;
  padding: 6px 12px; border-radius: 4px;
  transition: background 0.2s, color 0.2s;
  white-space: nowrap;
}
.main-nav a:hover, .main-nav a.active {
  background: rgba(255,255,255,0.15);
  color: white;
}
.nav-toggle { display: none; background: none; border: none; color: white; cursor: pointer; font-size: 1.4rem; }

/* ── Layout ─────────────────────────────────────── */
.site-wrapper {
  max-width: 1100px; margin: 0 auto;
  padding: 32px 24px;
  display: grid;
  grid-template-columns: 1fr 280px;
  gap: 32px;
}
.site-wrapper.full-width {
  grid-template-columns: 1fr;
  max-width: 800px;
}
main { min-width: 0; }
aside { min-width: 0; }

/* ── Hero (homepage) ───────────────────────────── */
.hero {
  background: var(--primary);
  color: white;
  padding: 56px 24px;
  text-align: center;
  margin-bottom: 0;
}
.hero h1 { font-size: 2.4rem; font-weight: 700; margin-bottom: 12px; }
.hero p { font-size: 1.1rem; color: rgba(255,255,255,0.8); max-width: 560px; margin: 0 auto; }

/* ── Post Cards ─────────────────────────────────── */
.post-card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 24px 28px;
  margin-bottom: 20px;
  box-shadow: var(--shadow);
  transition: transform 0.15s, box-shadow 0.15s;
}
.post-card:hover { transform: translateY(-2px); box-shadow: 0 6px 20px rgba(0,0,0,0.1); }
.post-card h2 { font-size: 1.25rem; margin-bottom: 8px; line-height: 1.3; }
.post-card h2 a { color: var(--primary); text-decoration: none; }
.post-card h2 a:hover { color: var(--accent); }
.post-meta {
  font-family: var(--font-ui);
  font-size: 0.8rem; color: var(--muted);
  display: flex; gap: 12px; align-items: center;
  margin-bottom: 10px; flex-wrap: wrap;
}
.cat-badge {
  background: var(--highlight); color: var(--accent);
  border-radius: 4px; padding: 2px 8px;
  font-size: 0.75rem; font-weight: 600;
  font-family: var(--font-ui);
  text-decoration: none;
}
.post-excerpt { color: var(--muted); font-size: 0.95rem; }
.read-more {
  display: inline-block; margin-top: 12px;
  font-family: var(--font-ui); font-size: 0.85rem; font-weight: 600;
  color: var(--accent); text-decoration: none;
  border: 1.5px solid var(--accent); border-radius: 4px;
  padding: 4px 14px; transition: background 0.2s, color 0.2s;
}
.read-more:hover { background: var(--accent); color: white; }

/* ── Article Page ───────────────────────────────── */
.article-header { margin-bottom: 28px; }
.article-header h1 { font-size: 2rem; line-height: 1.25; color: var(--primary); margin-bottom: 12px; }
.article-body { font-size: 1.05rem; }
.article-body p { margin-bottom: 1.2em; }
.article-body h2 { font-size: 1.4rem; color: var(--primary); margin: 1.8em 0 0.6em; }
.article-body h3 { font-size: 1.15rem; color: var(--primary); margin: 1.4em 0 0.5em; }
.article-body img { max-width: 100%; height: auto; border-radius: var(--radius); margin: 16px 0; }
.article-body table { width: 100%; border-collapse: collapse; margin: 20px 0; font-size: 0.95rem; }
.article-body th, .article-body td { border: 1px solid var(--border); padding: 10px 14px; text-align: left; }
.article-body th { background: var(--highlight); font-family: var(--font-ui); }
.article-body blockquote {
  border-left: 4px solid var(--accent); margin: 20px 0;
  padding: 12px 20px; background: var(--highlight);
  border-radius: 0 var(--radius) var(--radius) 0;
  font-style: italic; color: var(--muted);
}
.article-body ul, .article-body ol { margin: 0 0 1.2em 1.5em; }
.article-body li { margin-bottom: 6px; }
.article-body a { color: var(--accent); }

/* ── Sidebar ─────────────────────────────────────── */
.sidebar-widget {
  background: var(--card); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 20px 24px;
  margin-bottom: 24px; box-shadow: var(--shadow);
}
.sidebar-widget h3 {
  font-family: var(--font-ui); font-size: 0.8rem; font-weight: 700;
  text-transform: uppercase; letter-spacing: 1px;
  color: var(--muted); margin-bottom: 14px;
  padding-bottom: 8px; border-bottom: 1px solid var(--border);
}
.sidebar-widget ul { list-style: none; }
.sidebar-widget ul li { margin-bottom: 8px; }
.sidebar-widget ul li a {
  font-family: var(--font-ui); font-size: 0.9rem;
  color: var(--accent); text-decoration: none;
}
.sidebar-widget ul li a:hover { text-decoration: underline; }

/* ── Category Page ─────────────────────────────── */
.page-title {
  font-size: 1.6rem; color: var(--primary);
  margin-bottom: 8px; padding-bottom: 16px;
  border-bottom: 2px solid var(--border);
}
.page-subtitle { color: var(--muted); font-family: var(--font-ui); font-size: 0.9rem; margin-bottom: 28px; }

/* ── Footer ─────────────────────────────────────── */
.site-footer {
  background: var(--primary); color: rgba(255,255,255,0.6);
  text-align: center; padding: 32px 24px;
  font-family: var(--font-ui); font-size: 0.85rem;
  margin-top: 48px;
}
.site-footer a { color: rgba(255,255,255,0.8); }

/* ── Responsive ─────────────────────────────────── */
@media (max-width: 768px) {
  .site-wrapper { grid-template-columns: 1fr; padding: 20px 16px; }
  .main-nav { display: none; flex-direction: column; position: absolute;
              top: 64px; left: 0; right: 0; background: var(--primary);
              padding: 12px 16px; gap: 4px; }
  .main-nav.open { display: flex; }
  .nav-toggle { display: block; }
  .hero h1 { font-size: 1.7rem; }
  .article-header h1 { font-size: 1.5rem; }
}
"""

NAV_JS = """
<script>
document.addEventListener('DOMContentLoaded', function() {
  var btn = document.querySelector('.nav-toggle');
  var nav = document.querySelector('.main-nav');
  if (btn && nav) {
    btn.addEventListener('click', function() { nav.classList.toggle('open'); });
  }
});
</script>
"""

def build_nav(categories, path_prefix, active_cat=None):
    links = f'<a href="{path_prefix}index.html">Domů</a>\n'
    for cat in categories:
        slug = cat['slug']
        name = cat['name']
        active = ' class="active"' if cat['name'] == active_cat else ''
        links += f'<a href="{path_prefix}kategorie/{slug}.html"{active}>{name}</a>\n'
    return links

def build_sidebar(posts, categories, path_prefix):
    # Recent posts
    recent = ''.join(
        f'<li><a href="{path_prefix}clanky/{p["slug"]}.html">{p["title"]}</a></li>'
        for p in posts[:8]
    )
    # Categories
    cat_links = ''.join(
        f'<li><a href="{path_prefix}kategorie/{c["slug"]}.html">{c["name"]}</a></li>'
        for c in categories
    )
    return f"""
    <aside>
      <div class="sidebar-widget">
        <h3>Nejnovější články</h3>
        <ul>{recent}</ul>
      </div>
      <div class="sidebar-widget">
        <h3>Kategorie</h3>
        <ul>{cat_links}</ul>
      </div>
    </aside>"""

def page_shell(title, body, site_name, site_desc, categories, path_prefix, active_cat=None, full_width=False):
    nav = build_nav(categories, path_prefix, active_cat)
    wrapper_class = "site-wrapper full-width" if full_width else "site-wrapper"
    return f"""<!DOCTYPE html>
<html lang="cs">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(title)} | {html.escape(site_name)}</title>
  <link rel="stylesheet" href="{path_prefix}style.css">
</head>
<body>
<header class="site-header">
  <div class="header-inner">
    <a class="site-logo" href="{path_prefix}index.html">{html.escape(site_name)}</a>
    <button class="nav-toggle" aria-label="Menu">☰</button>
    <nav class="main-nav">{nav}</nav>
  </div>
</header>
{body}
<footer class="site-footer">
  <p>&copy; {datetime.now().year} {html.escape(site_name)} &mdash; {html.escape(site_desc)}</p>
</footer>
{NAV_JS}
</body>
</html>"""

def make_excerpt(content, length=160):
    text = re.sub(r'<[^>]+>', '', content)
    text = html.unescape(text).strip()
    return text[:length].rsplit(' ', 1)[0] + '…' if len(text) > length else text

def format_date(date_str):
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
        months = ['ledna','února','března','dubna','května','června',
                  'července','srpna','září','října','listopadu','prosince']
        return f"{dt.day}. {months[dt.month-1]} {dt.year}"
    except:
        return date_str[:10]

def generate_site(sql_bytes, site_name, site_desc):
    sql_text = sql_bytes.decode('utf-8', errors='replace')
    posts, raw_cats = parse_sql(sql_text)

    # Deduplicate categories by name, keep ones that have posts
    cat_names_used = set()
    for p in posts:
        for c in p['categories']:
            cat_names_used.add(c)

    seen = set()
    categories = []
    for cat in raw_cats:
        if cat['name'] not in seen and cat['name'] in cat_names_used:
            seen.add(cat['name'])
            categories.append(cat)

    files = {}

    # ── style.css ────────────────────────────────────────────────
    files['style.css'] = SITE_CSS

    # ── Homepage ─────────────────────────────────────────────────
    cards = ''
    for p in posts:
        if p['post_type'] != 'post':
            continue
        cats_html = ''.join(
            f'<a class="cat-badge" href="../kategorie/{re.sub(chr(32), "-", c.lower())}.html">{c}</a>'
            for c in p['categories']
        )
        excerpt = make_excerpt(p['content'])
        cards += f"""
        <div class="post-card">
          <div class="post-meta">
            <span>📅 {format_date(p['date'])}</span>
            <span>✍️ {html.escape(p['author'])}</span>
            {cats_html}
          </div>
          <h2><a href="clanky/{p['slug']}.html">{html.escape(p['title'])}</a></h2>
          <p class="post-excerpt">{html.escape(excerpt)}</p>
          <a class="read-more" href="clanky/{p['slug']}.html">Číst dál →</a>
        </div>"""

    hero = f"""
    <div class="hero">
      <h1>{html.escape(site_name)}</h1>
      <p>{html.escape(site_desc)}</p>
    </div>"""

    homepage_body = hero + f'<div class="site-wrapper"><main>{cards}</main>{build_sidebar(posts, categories, "./")}</div>'
    files['index.html'] = page_shell('Domů', homepage_body, site_name, site_desc, categories, './')

    # ── Article pages ─────────────────────────────────────────────
    for p in posts:
        if p['post_type'] != 'post':
            continue
        cats_html = ' '.join(
            f'<a class="cat-badge" href="../kategorie/{re.sub(chr(32), "-", c.lower())}.html">{c}</a>'
            for c in p['categories']
        )
        body = f"""
        <div class="site-wrapper">
          <main>
            <div class="post-card">
              <div class="article-header">
                <div class="post-meta">
                  <span>📅 {format_date(p['date'])}</span>
                  <span>✍️ {html.escape(p['author'])}</span>
                  {cats_html}
                </div>
                <h1>{html.escape(p['title'])}</h1>
              </div>
              <div class="article-body">{p['content']}</div>
            </div>
          </main>
          {build_sidebar(posts, categories, '../')}
        </div>"""
        files[f"clanky/{p['slug']}.html"] = page_shell(
            p['title'], body, site_name, site_desc, categories, '../'
        )

    # ── Static pages ──────────────────────────────────────────────
    for p in posts:
        if p['post_type'] != 'page':
            continue
        body = f"""
        <div class="site-wrapper full-width">
          <main>
            <div class="post-card">
              <div class="article-header"><h1>{html.escape(p['title'])}</h1></div>
              <div class="article-body">{p['content']}</div>
            </div>
          </main>
        </div>"""
        files[f"stranky/{p['slug']}.html"] = page_shell(
            p['title'], body, site_name, site_desc, categories, '../'
        )

    # ── Category pages ────────────────────────────────────────────
    for cat in categories:
        cat_posts = [p for p in posts if cat['name'] in p['categories'] and p['post_type'] == 'post']
        cards = ''
        for p in cat_posts:
            excerpt = make_excerpt(p['content'])
            cards += f"""
            <div class="post-card">
              <div class="post-meta"><span>📅 {format_date(p['date'])}</span><span>✍️ {html.escape(p['author'])}</span></div>
              <h2><a href="../clanky/{p['slug']}.html">{html.escape(p['title'])}</a></h2>
              <p class="post-excerpt">{html.escape(excerpt)}</p>
              <a class="read-more" href="../clanky/{p['slug']}.html">Číst dál →</a>
            </div>"""
        body = f"""
        <div class="site-wrapper">
          <main>
            <h1 class="page-title">📂 {html.escape(cat['name'])}</h1>
            <p class="page-subtitle">{len(cat_posts)} článků v této kategorii</p>
            {cards}
          </main>
          {build_sidebar(posts, categories, '../')}
        </div>"""
        files[f"kategorie/{cat['slug']}.html"] = page_shell(
            cat['name'], body, site_name, site_desc, categories, '../', active_cat=cat['name']
        )

    # ── RSS feed ──────────────────────────────────────────────────
    rss_items = ''
    for p in posts[:20]:
        if p['post_type'] != 'post':
            continue
        rss_items += f"""
    <item>
      <title><![CDATA[{p['title']}]]></title>
      <link>clanky/{p['slug']}.html</link>
      <pubDate>{p['date']}</pubDate>
      <description><![CDATA[{make_excerpt(p['content'])}]]></description>
    </item>"""
    files['feed.xml'] = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>{site_name}</title>
    <description>{site_desc}</description>
    <language>cs</language>
    {rss_items}
  </channel>
</rss>"""

    return files, posts, categories


# ══════════════════════════════════════════════════════════════════
# STREAMLIT UI
# ══════════════════════════════════════════════════════════════════

uploaded = st.file_uploader("📂 Upload SQL database (.sql)", type=["sql"])

if uploaded:
    # Auto-detect site name from filename
    raw_name = uploaded.name.rsplit('.', 1)[0]
    default_name = re.sub(r'[-_]?(export|backup|db|dump)[-_]?', '', raw_name, flags=re.IGNORECASE).strip('-_ ').capitalize()
    if not default_name:
        default_name = "Můj web"

    col1, col2 = st.columns(2)
    with col1:
        site_name = st.text_input("Název webu", value=default_name)
    with col2:
        site_desc = st.text_input("Popis webu", value="Informační portál")

    zip_filename = f"{site_name}.zip"

    if st.button("🚀 Generovat web", type="primary"):
        with st.spinner("Čistím malware a generuji stránky…"):
            sql_bytes = uploaded.getvalue()
            files, posts, categories = generate_site(sql_bytes, site_name, site_desc)

        # Stats
        n_posts = sum(1 for p in posts if p['post_type'] == 'post')
        n_pages = sum(1 for p in posts if p['post_type'] == 'page')
        n_cats = len(categories)
        n_files = len(files)

        st.success(f"✅ Web vygenerován! {n_files} souborů připraveno.")

        col1, col2, col3 = st.columns(3)
        col1.metric("Články", n_posts)
        col2.metric("Stránky + kategorie", n_pages + n_cats)
        col3.metric("Celkem souborů", n_files)

        # Check for posts with no content
        empty = [p['title'] for p in posts if not p['content'] and p['post_type'] == 'post']
        if empty:
            st.markdown(f'<div class="warn">⚠️ {len(empty)} článků bez obsahu (pravděpodobně smazáno hackery): {", ".join(empty[:5])}{"…" if len(empty) > 5 else ""}</div>', unsafe_allow_html=True)

        # Build ZIP
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for path, content in files.items():
                zf.writestr(path, content.encode('utf-8') if isinstance(content, str) else content)

        st.download_button(
            label=f"⬇️ Stáhnout {zip_filename}",
            data=zip_buf.getvalue(),
            file_name=zip_filename,
            mime="application/zip"
        )

        st.markdown("---")
        st.markdown("""
**Další kroky:**
1. Rozbal ZIP → dostaneš složku se soubory
2. Zkopíruj `wp-content/` ze starého WordPress webu do rozbalené složky
3. Přejdi na **Cloudflare → Workers & Pages → Pages → Upload assets**
4. Přetáhni celý obsah složky do Cloudflare
5. ✅ Web je živý!
        """)
