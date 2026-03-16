import streamlit as st
import xml.etree.ElementTree as ET
import zipfile
import io
import re

st.set_page_config(page_title="Professional Static Migrator", layout="wide")

st.title("🗞️ Magazine-Style Static Generator")
st.markdown("Upload your XML to create a responsive site with a header menu and clean design.")

def clean_malware(content):
    if not content: return ""
    content = re.sub(r'<meta http-equiv="refresh".*?>', '', content, flags=re.IGNORECASE)
    content = re.sub(r'<script.*?>.*?</script>', '', content, flags=re.DOTALL)
    return content

def get_template(title, content, site_name, is_subfolder=False):
    path_prefix = "../" if is_subfolder else "./"
    
    return f"""<!DOCTYPE html>
<html lang="cs">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} | {site_name}</title>
    <link rel="stylesheet" href="{path_prefix}style.css">
</head>
<body>
    <header class="site-header">
        <div class="container header-flex">
            <div class="logo"><a href="{path_prefix}index.html">{site_name}</a></div>
            <nav class="main-nav">
                <a href="{path_prefix}index.html">Domů</a>
                <a href="{path_prefix}pages/o-nas.html">O nás</a>
                <a href="{path_prefix}pages/kontakt.html">Kontakt</a>
            </nav>
        </div>
    </header>

    <main class="container">
        <article class="content-area">
            {content}
        </article>
    </main>

    <footer class="site-footer">
        <div class="container">
            <p>&copy; {site_name}. Všechna práva vyhrazena.</p>
        </div>
    </footer>
</body>
</html>"""

def get_css():
    return """
    :root { --primary: #2c3e50; --accent: #e74c3c; --text: #333; --bg: #f4f7f6; }
    body { font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; margin: 0; background: var(--bg); color: var(--text); line-height: 1.6; }
    .container { max-width: 1000px; margin: 0 auto; padding: 0 20px; }
    
    .site-header { background: #fff; border-bottom: 3px solid var(--primary); padding: 20px 0; position: sticky; top: 0; z-index: 100; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
    .header-flex { display: flex; justify-content: space-between; align-items: center; }
    .logo a { font-size: 1.8rem; font-weight: 800; color: var(--primary); text-decoration: none; text-transform: uppercase; letter-spacing: 1px; }
    .main-nav a { margin-left: 20px; text-decoration: none; color: var(--primary); font-weight: 600; transition: color 0.3s; }
    .main-nav a:hover { color: var(--accent); }

    .content-area { background: #fff; padding: 40px; margin-top: 30px; border-radius: 4px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); }
    h1 { font-size: 2.5rem; color: var(--primary); margin-top: 0; line-height: 1.2; }
    .post-item { background: #fff; padding: 20px; margin-bottom: 20px; border-radius: 4px; border-left: 5px solid var(--primary); transition: transform 0.2s; }
    .post-item:hover { transform: translateY(-3px); }
    .post-item h2 a { text-decoration: none; color: var(--primary); }
    
    img { max-width: 100%; height: auto; border-radius: 4px; margin: 20px 0; }
    .site-footer { text-align: center; padding: 40px 0; color: #777; font-size: 0.9rem; }

    @media (max-width: 600px) {
        .header-flex { flex-direction: column; }
        .main-nav { margin-top: 15px; }
        h1 { font-size: 1.8rem; }
        .content-area { padding: 20px; }
    }
    """

def process_xml(uploaded_file):
    NS = {
        'content': "http://purl.org/rss/1.0/modules/content/",
        'wp': "http://wordpress.org/export/1.2/",
        'dc': "http://purl.org/dc/elements/1.1/"
    }
    
    # Try parsing the XML
    try:
        tree = ET.parse(uploaded_file)
        root = tree.getroot()
    except Exception as e:
        st.error(f"Error parsing XML: {e}")
        return None

    channel = root.find('channel')
    site_name = channel.find('title').text or "Můj Web"
    
    files = {}
    files['style.css'] = get_css()
    posts_data = []

    for item in channel.findall('item'):
        title = item.find('title').text or "Untitled"
        # CRITICAL: We use namespaces to get the actual content
        content_el = item.find('content:encoded', NS)
        content = content_el.text if content_el is not None else ""
        
        slug = item.find('wp:post_name', NS).text
        post_type = item.find('wp:post_type', NS).text
        status = item.find('wp:status', NS).text

        if status == 'publish' and post_type in ['post', 'page']:
            clean_text = clean_malware(content)
            # Fix Image Paths
            clean_text = re.sub(r'https?://[^/]+/wp-content/', '/wp-content/', clean_text)
            
            is_sub = (post_type == 'post' or post_type == 'page')
            folder = "posts" if post_type == 'post' else "pages"
            full_path = f"{folder}/{slug}.html"
            
            html_body = f"<h1>{title}</h1>{clean_text}"
            files[full_path] = get_template(title, html_body, site_name, is_subfolder=True)
            
            if post_type == 'post':
                posts_data.append({'title': title, 'url': full_path})

    # Homepage (Index)
    index_content = "<h1>Nejnovější články</h1><div class='post-grid'>"
    for p in posts_data:
        index_content += f"<div class='post-item'><h2><a href='{p['url']}'>{p['title']}</a></h2></div>"
    index_content += "</div>"
    
    files['index.html'] = get_template("Domů", index_content, site_name, is_subfolder=False)
    
    return files, site_name

# UI
uploaded_file = st.file_uploader("Upload WordPress XML", type="xml")

if uploaded_file:
    if st.button("Generate Site"):
        result = process_xml(uploaded_file)
        if result:
            files, s_name = result
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w") as z:
                for path, data in files.items():
                    z.writestr(path, data)
            
            st.success(f"Site '{s_name}' ready!")
            st.download_button(f"Download {s_name}.zip", zip_buffer.getvalue(), f"{s_name}.zip")
