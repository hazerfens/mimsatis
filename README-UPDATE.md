# Mim Satış CRM güncellemesi

Coolify kalıcı disk bağlantıları `/srv/mim-satis/data` → `/app/data` ve `/srv/mim-satis/uploads` → `/app/uploaded_files` olarak devam etmelidir. Üretim dosyası `DATABASE_PATH=/app/data/mim_reklam_satis_sistemi.db` ile seçilir. `SECRET_KEY` mevcut değeriyle korunmalıdır. `REQUIRE_EXISTING_DB=1` dosya bulunamadığında başlangıcı durdurur.

Deploy sırasında eski uygulamanın yazma trafiğini durdurun. Aynı SQLite dosyasına eski ve yeni sürümü birlikte yazdırmayın. Veritabanı ve yüklenen dosyalar için ayrıca sunucu yedeği alın.

Docker başlangıcı `python migrate_sqlite.py` çalıştırır. Geçiş, veritabanının SQLite backup API ile tutarlı kopyasını aynı veri klasöründeki `backups/` altında oluşturur. Yeni şema, kayıt aktarımı ve geçiş işareti tek transaction içinde tamamlanır. Hata durumunda transaction geri alınır ve web sunucusu başlamaz.

Eski customer, interaction, proposal, task, user ve email_template kayıtları yeni customers, interactions, quotes, tasks, staff ve mail_templates tablolarına kimlikleri korunarak aktarılır. Eski sütunlar yeni tablolarda da tutulur. Görev tarih ve saati ayrılır, teklif kalemleri yeni arayüz biçimine çevrilir. Firma ayarları aktarılır. Mevcut kullanıcı adları ve parola hashleri giriş için korunur. SMTP parolaları bootstrap API yanıtına dahil edilmez.

Eski tabloların tamamı veritabanında tutulur. Belgeler, satış/ödeme, üretim kayıtları ve eski sistem ayarları da bu kapsamdadır. Yeni arayüzde bu eski modüllerin karşılığı bulunmadığından bu kayıtlara arayüz erişimi ayrıca geliştirilmelidir. Yüklenen dosyalar mevcut uploads diskinde kalır. Yeni uygulama eski tabloları güncel tutmaz. Geri dönüş gerektiğinde deploy öncesi yedek ve o tarihten sonraki yeni kayıtlar birlikte değerlendirilmelidir.

Başarılı geçiş `crm_migrations` tablosunda işaretlenir. Sonraki deploylar eski kayıtları yeniden kopyalamaz. Hem eski hem yeni eş tablolar doluysa ilk aktarım otomatik birleştirme yapmadan hata verir ve transaction geri alınır.

Yeni kurulumda `REQUIRE_EXISTING_DB=0`, en az 12 karakterli `ADMIN_INITIAL_PASSWORD` ve `SECRET_KEY` gerekir. Mevcut üretim veritabanında bu kurulum seçeneğini kullanmayın.

Deploy sonrasında loglarda `SQLite backup created` ve `CRM schema ready` mesajlarını, mevcut kullanıcıyla girişi, müşteri/teklif/görev sayılarını ve yüklenen dosyaları kontrol edin. Kullanıcının sağladığı mevcut veritabanının ayrı kopyasında 2657 müşteri, 6 kullanıcı ve 1 görev aktarımı, ikinci geçişte kayıt sayılarının korunması, SQLite bütünlüğü ve API yanıtı doğrulanmıştır. Kaynak dosyanın SHA-256 özeti test öncesi ve sonrasında aynıdır. Veritabanı dosyaları Git deposuna eklenmez.
