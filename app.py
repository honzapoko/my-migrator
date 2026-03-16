import streamlit as st
import re
import zipfile
import io

st.set_page_config(page_title="SQL to Static Migrator", layout="wide")

st.title("🛡️ SQL-to-Static Professional Migrator")
st.info("The XML file was empty! This tool reads the SQL database directly to recover your articles.")

def clean_malware(content):
    if not content: return ""
    # Strip urshort redirects and script injections
    content = re.sub(r'<meta http-equiv="refresh".*?>', '', content, flags=re.IGNORECASE)
    content = re.sub(r'<script.*?>.*?</script>', '', content, flags=re.DOTALL)
    # Clean SQL-specific escaping and line breaks
    content = content.replace("\\'", "'").replace('\\"', '"').replace('\\r\\n', '<br>')
    return content

def get_template(title, content, site_name, is_subfolder=False):
    path_prefix = "../" if is_subfolder else "./"
    return f"""<!DOCTYPE html>
<html lang="cs">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} | {site_name}</title>
    <style>
        :root {{ --primary: #2c3e50; --accent: #e74c3c; --text: #333; --bg: #f4f7f6; }}
        body {{ font-family: sans-serif; margin: 0; background: var(--bg); color: var(--text); line-height: 1.6; padding-bottom: 50px; }}
        .container {{ max-width: 900px; margin: 0 auto; padding: 0 20px; }}
        .site-header {{ background: #fff; border-bottom: 3px solid var(--primary); padding: 20px 0; margin-bottom: 30px; }}
        .logo a {{ font-size: 1.5rem; font-weight: bold; color: var(--primary); text-decoration: none; }}
        .content-area {{ background: #fff; padding: 40px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
        h1 {{ color: var(--primary); margin-top: 0; }}
        img {{ max-width: 100%; height: auto; border-radius: 4px; }}
        .post-card {{ background: #fff; padding: 15px; margin-bottom: 12px; border-radius: 5px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border-left: 4px solid var(--primary); }}
        .post-card a {{ text-decoration: none; color: #007bff; font-weight: bold; font-size: 1.1rem; }}
    </style>
</head>
<body>
    <header class="site-header"><div class="container"><div class="logo"><a href="{path_prefix}index.html">{site_name}</a></div></div></header>
    <main class="container"><div class="content-area">{content}</div></main>
</body>
</html>"""

def parse_sql_posts(sql_text):
    # Regex for standard WordPress INSERT pattern: (ID, author, date, date_gmt, content, title, excerpt, status...)
    pattern = r"\((\d+),\s*\d+,\s*'[^']*',\s*'[^']*',\s*'(.*?)',\s*'(.*?)',\s*'[^']*',\s*'publish',\s*'[^']*',\s*'[^']*',\s*'[^']*',\s*'(.*?)',"
    matches = re.findall(pattern, sql_text, re.DOTALL)
    
    posts = []
    for m in matches:
        post_id, content, title, slug = m
        if len(content) > 100: # Ensure we are grabbing actual articles, not placeholders
            posts.append({
                'title': title,
                'content': clean_malware(content),
                'slug': slug if slug else f"post-{post_id}"
            })
    return posts

uploaded_file = st.file_uploader("Upload your SQL database file (.sql)", type="sql")

if uploaded_file:
    sql_text = uploaded_file.getvalue().decode('utf-8', errors='ignore')
    site_name = st.text_input("Site Name", "Adehade")
    
    if st.button("Recover and Generate Site"):
        posts = parse_sql_posts(sql_text)
        
        if not posts:
            st.error("No articles found. Ensure the SQL dump includes the 'wp_posts' table and 'publish' status.")
        else:
            st.success(f"Recovered {len(posts)} articles from the database!")
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w") as z:
                index_html = "<h1>Nejnovější články</h1>"
                for p in posts:
                    file_path = f"posts/{p['slug']}.html"
                    z.writestr(file_path, get_template(p['title'], f"<h1>{p['title']}</h1>{p['content']}", site_name, True))
                    index_html += f"<div class='post-card'><a href='{file_path}'>{p['title']}</a></div>"
                
                z.writestr("index.html", get_template("Domů", index_html, site_name, False))
            
            st.download_button(f"Download {site_name}.zip", zip_buffer.getvalue(), f"{site_name}.zip")
