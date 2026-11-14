# Bar Radar
Haftalık otomatik pazar araştırması: yeni açılmış (<=18 ay), 4.0+ yıldızlı kokteyl barları bulur,
Google Maps yorumlarını Gemini ile Türkçe özetler. Sonuç: docs/results.json + docs/index.html (GitHub Pages).

Kurulum: GitHub repo (public) -> Settings > Secrets > Actions: APIFY_TOKEN, GEMINI_API_KEY ->
Settings > Pages > main / docs -> Actions > weekly-bar-radar > Run workflow.
Ayarlar: config.json (şehirler, filtreler, bütçe tavanları).
