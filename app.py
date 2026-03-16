import streamlit as st
import xml.etree.ElementTree as ET
import zipfile
import io
import re
from datetime import datetime

st.set_page_config(page_title="WP Static Migrator", page_icon="🛡️")

st.title("🛡️ WordPress to Static Migrator")
st.info("Upload one WordPress XML export at a time to clean malware and convert to a flat HTML site.")

# --- Enhanced Malware Cleaning ---
def clean_malware(content):
    if not content: return ""
    # Remove urshort.com redirects
    content = re.sub(r'<meta http-equiv="refresh".*?urshort\.com.*?>', '', content, flags=re.IGNORECASE)
    # Remove obfuscated JS malware (the _0x... scripts)
    content = re.sub(r'<script.*?>.*?_0x[0-9a-f]+.*?</script>', '', content, flags=re.DOTALL)
    # Remove window.location hijacks
    content = re.sub(r'<script.*?>.*?window\.location\.href.*?=.*?</script>', '', content, flags=re.DOTALL)
    return content

# --- Site Generation ---
def generate_site(xml_data, site_name):
    NS = {'wp': "http://wordpress.org/export/1.2/", 'content': "http://purl.org/rss/1.0/modules/content/"}
    tree = ET.parse(io.BytesIO(xml_data))
    root = tree.getroot()
    channel = root.find('channel')
    
    files = {}
    posts_list = []
    
    # Modern Minimal CSS
    css = """
    body { font-family: -apple-system, sans-serif; line-height: 1.6; color: #333; max-width: 800px; margin: 0 auto; padding: 2rem; background: #fdfdfd; }
    img { max-width: 100%; height: auto; border-radius: 8px; }
    nav { margin-bottom: 2rem; border-bottom: 1px solid #eee; padding-bottom: 1rem; }
    nav a { margin-right: 1rem; text-decoration: none; color: #007bff; font-weight: bold; }
    h1 { color: #111; }
    .post-meta { font-size: 0.9rem; color: #666; margin-bottom: 2rem; }
    .content { margin-top: 2rem; }
    ul { list-style: none; padding: 0; }
    li { margin-bottom: 1rem; padding: 1rem; border: 1px solid #eee; border-radius: 8px; }
    li a { text-decoration: none; font-size: 1.2rem; color: #007bff; }
    """
    files['style.css'] = css

    # Navigation HTML snippet
    nav_html = f"<nav><a href='/'>Home</a></nav>"

    for item in channel.findall('item'):
        title = item.find('title').text or "Untitled"
        content = item.find('content:encoded', NS).text or ""
        slug = item.find('wp:post_name', NS).text or "index"
        post_type = item.find('wp:post_type', NS).text
        status = item.find('wp:status', NS).text

        if status == 'publish' and post_type in ['post', 'page']:
            clean_text = clean_malware(content)
            
            # Make image URLs relative so they work with your uploaded wp-content folder
            # This looks for /wp-content/ and ensures it works from any subfolder
            clean_text = re.sub(r'https?://[^/]+/wp-content/', '/wp-content/', clean_text)
            
            html = f"""<!DOCTYPE html>
            <html lang="cs">
            <head><meta charset="UTF-8"><title>{title}</title><link rel="stylesheet" href="/style.css"></head>
            <body>{nav_html}<h1>{title}</h1><div class="content">{clean_text}</div></body>
            </html>"""
            
            # Save files into specific directories
            folder = "posts" if post_type == "post" else "pages"
            files[f"{folder}/{slug}.html"] = html
            if post_type == 'post':
                posts_list.append((title, f"posts/{slug}.html"))

    # Generate the Homepage (Index)
    index_links = "".join([f"<li><a href='/{p[1]}'>{p[0]}</a></li>" for p in posts_list])
    files["index.html"] = f"""<!DOCTYPE html>
    <html lang="cs">
    <head><meta charset="UTF-8"><title>{site_name}</title><link rel="stylesheet" href="/style.css"></head>
    <body><h1>{site_name}</h1><ul>{index_links}</ul></body>
    </html>"""
    
    return files

# --- UI Logic ---
uploaded_file = st.file_uploader("Upload WordPress XML", type="xml")

if uploaded_file:
    # Logic to rename Adehade-export.xml -> Adehade
    raw_name = uploaded_file.name.rsplit('.', 1)[0]
    clean_name = raw_name.replace('-export', '').replace('_export', '').capitalize()
    zip_filename = f"{clean_name}.zip"
    
    st.write(f"📂 Detected Site: **{clean_name}**")

    if st.button("Generate Site"):
        with st.spinner("Cleaning malware and building files..."):
            site_files = generate_site(uploaded_file.getvalue(), clean_name)
            
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                for path, content in site_files.items():
                    zip_file.writestr(path, content)
            
            st.success(f"Site generated successfully as {zip_filename}!")
            st.download_button(
                label=f"Download {zip_filename}",
                data=zip_buffer.getvalue(),
                file_name=zip_filename,
                mime="application/zip"
            )
