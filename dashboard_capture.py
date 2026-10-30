import asyncio
import io
import os
import logging
from datetime import datetime
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

DASHBOARD_URL = os.getenv(
    "DASHBOARD_URL",
    "https://script.google.com/macros/s/AKfycbwsleRWZLr24S6AT-cSG0CstUeSl8xIA3ssnSsZ-Hf3c50ep_ko07G-qxMrs35yqUoAvQ/exec"
)

async def capture_productivity_dashboard():
    """
    Mengambil screenshot tabel Rekapitulasi Per Teknisi dari dashboard Assurance
    dan mengekstrak data statistik untuk deskripsi analisis.
    """
    async with async_playwright() as p:
        # Konfigurasi Chromium headless yang aman untuk lokal dan cloud (Railway)
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu"
            ]
        )
        page = await browser.new_page(
            viewport={"width": 1400, "height": 850},
            device_scale_factor=2
        )
        
        try:
            logger.info("Membuka dashboard Assurance...")
            await page.goto(DASHBOARD_URL, wait_until="networkidle", timeout=45000)
            await page.wait_for_timeout(3000)
            
            # Cari frame userHtmlFrame yang berisi aplikasi dashboard
            frame = page.frame(name="userHtmlFrame")
            if not frame:
                for f in page.frames:
                    if "googleusercontent.com" in f.url:
                        frame = f
                        break
                        
            if not frame:
                frame = page.main_frame
                
            # Tunggu hingga baris tabel teknisi dimuat
            await frame.wait_for_selector("table tbody tr", timeout=20000)
            
            # Sembunyikan toolbar filter, kartu atas, dan grafik donut agar tampilan
            # fokus pada Header Laporan + Tabel Rekapitulasi Teknisi (persis seperti permintaan user)
            await frame.evaluate("""() => {
                const hideSelectors = ['.toolbar', '.cards', '.dashboard-grid', '.sidebar'];
                hideSelectors.forEach(sel => {
                    const el = document.querySelector(sel);
                    if (el) el.style.display = 'none';
                });
            }""")
            await page.wait_for_timeout(500)
            
            # Ekstrak data teks dari tabel untuk deskripsi laporan
            rows = await frame.query_selector_all("table tbody tr")
            tech_data = []
            
            total_all_incident = 0
            total_all_close = 0
            total_all_open = 0
            total_all_update = 0
            
            for row in rows:
                cols = await row.query_selector_all("td")
                if len(cols) >= 12:
                    name = (await cols[0].inner_text()).strip()
                    total = int((await cols[1].inner_text()).strip() or 0)
                    open_tot = int((await cols[5].inner_text()).strip() or 0)
                    update_tot = int((await cols[6].inner_text()).strip() or 0)
                    close_tot = int((await cols[10].inner_text()).strip() or 0)
                    pct_close = (await cols[11].inner_text()).strip()
                    
                    total_all_incident += total
                    total_all_close += close_tot
                    total_all_open += open_tot
                    total_all_update += update_tot
                    
                    tech_data.append({
                        "name": name,
                        "total": total,
                        "open": open_tot,
                        "update": update_tot,
                        "close": close_tot,
                        "pct": pct_close
                    })
                    
            # Ambil tanggal dari topbar dashboard jika ada
            date_el = await frame.query_selector("#topDate")
            date_text = (await date_el.inner_text()).strip() if date_el else datetime.now().strftime("%d-%m-%Y")
            if not date_text or date_text == "-":
                date_text = datetime.now().strftime("%d-%m-%Y")
                
            # Ambil screenshot elemen content
            content_elem = await frame.query_selector(".content") or await frame.query_selector("body")
            screenshot_bytes = await content_elem.screenshot()
            
            # Hitung persentase keseluruhan
            overall_pct = (total_all_close / total_all_incident * 100) if total_all_incident > 0 else 0
            
            # Pisahkan top performer dan yang butuh perhatian
            top_performers = [t for t in tech_data if "100" in t["pct"]]
            need_attention = [t for t in tech_data if t["pct"] not in ["100.0%", "100%"] and (t["open"] > 0 or t["update"] > 0)]
            
            # Susun Deskripsi Laporan
            waktu_cek = datetime.now().strftime("%H:%M WIB")
            desc_lines = [
                f"📊 *LAPORAN REKAP PRODUKTIVITAS TEKNISI*",
                f"📅 *Tanggal:* `{date_text}` | 🕒 *Pukul:* `{waktu_cek}`",
                "━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"👷 *Total Tim Teknisi:* {len(tech_data)} Tim",
                f"🎫 *Total Incident:* {total_all_incident} Tiket",
                f"✅ *Selesai (Close):* {total_all_close} ({overall_pct:.1f}%)",
                f"🔄 *Dalam Update:* {total_all_update}",
                f"⏳ *Open Progress:* {total_all_open}",
                "━━━━━━━━━━━━━━━━━━━━━━━━━"
            ]
            
            if top_performers:
                top_names = ", ".join([f"`{t['name']}`" for t in top_performers[:5]])
                desc_lines.append(f"🏆 *Top Performer (100% Close):* {top_names}")
                
            if need_attention:
                desc_lines.append("\n⚠️ *Tiket Tertunda / Perlu Follow-Up:*")
                for t in need_attention[:6]:
                    desc_lines.append(f"• `{t['name']}`: Open {t['open']} | Update {t['update']} | Close {t['pct']}")
                    
            desc_lines.append("\n_Tangkapan layar dashboard terlampir di atas._")
            description = "\n".join(desc_lines)
            
            return screenshot_bytes, description
            
        finally:
            await browser.close()

if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    async def _test():
        img_bytes, desc = await capture_productivity_dashboard()
        print("Success!")
        print("Image size:", len(img_bytes), "bytes")
        print("\nDescription:\n", desc)
    asyncio.run(_test())

