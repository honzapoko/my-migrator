import streamlit as st
import xml.etree.ElementTree as ET
import zipfile
import io
import os
import re
from datetime import datetime

st.set_page_config(page_title="WP to Static Migrator", page_icon="🚀")

st.title("🌐 WP to Static Site Dashboard")
st.markdown("Upload your WordPress XML exports. This tool cleans malware, fixes links, and builds static HTML sites.")

# --- Malware Cleaning Logic ---
def clean_malware(content):
    if not content: return ""
    # Remove urshort.com redirects
    content = re.sub(r'<meta http-equiv="refresh".*?urshort\.com.*?>', '', content, flags=re.IGNORECASE)
    # Remove obfuscated JS malware (the _0x... scripts)
    content = re.sub(r'<script.*?>.*?_0x[0-9a-f]+.*?</script>', '', content, flags=re.DOTALL)
    # Remove common window.location hijacks
    content = re.sub(r'<script.*?>.*?window\.location\.href.*?=.*?</script>', '', content, flags=re.DOTALL)
    return content

# --- Site Generation Logic ---
def generate_site_files(xml_data, site_name):
    NS = {'wp': "http://wordpress.org/export/1.2/", 'content': "http://purl.org/rss/1.0/modules/content/"}
    tree = ET.parse(io.BytesIO(xml_data))
    root = tree.getroot()
    channel = root.find('channel')
    
    files = {}
    posts_list = []
    
    # Simple CSS
    css = "body{font-family:sans-serif;line-height:1.6;margin:40px;color:#333} img{max-width:100%} .container{max-width:800px;margin:auto}"
    files['style.css'] = css

    for item in channel.findall('item'):
        title = item.find('title').text or "Untitled"
        content = item.find('content:encoded', NS).text or ""
        slug = item.find('wp:post_name', NS).text or "index"
        post_type = item.find('wp:post_type', NS).text
        status = item.find('wp:status', NS).text

        if status == 'publish' and post_type in ['post', 'page']:
            clean_text = clean_malware(content)
            # Fix WordPress image URLs to be relative
            clean_text = clean_text.replace('https://' + site_name + '/', '/')
            clean_text = clean_text.replace('/wp-content/', '../wp-content/')
            
            html = f"<html><head><link rel='stylesheet' href='../style.css'></head><body><div class='container'><h1>{title}</h1>{clean_text}</div></body></html>"
            
            folder = "posts" if post_type == "post" else "pages"
            files[f"{folder}/{slug}.html"] = html
            if post_type == 'post':
                posts_list.append((title, f"posts/{slug}.html"))

    # Homepage
    index_links = "".join([f"<li><a href='{p[1]}'>{p[0]}</a></li>" for p in posts_list])
    files["index.html"] = f"<html><head><link rel='stylesheet' href='style.css'></head><body><div class='container'><h1>{site_name}</h1><ul>{index_links}</ul></div></body></html>"
    
    return files

# --- UI Layout ---
uploaded_files = st.file_uploader("Upload WordPress XML files", type="xml", accept_multiple_files=True)

if uploaded_files:
    if st.button("Generate All Sites"):
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED) as zip_file:
            for uploaded_file in uploaded_files:
                site_name = uploaded_file.name.replace(".xml", "")
                st.write(f"Processing: {site_name}...")
                
                site_files = generate_site_files(uploaded_file.getvalue(), site_name)
                for path, content in site_files.items():
                    zip_file.writestr(f"{site_name}/{path}", content)
        
        st.success("All sites processed!")
        st.download_button(
            label="Download All Sites (.zip)",
            data=zip_buffer.getvalue(),
            file_name="migrated_sites.zip",
            mime="application/zip"
        )
