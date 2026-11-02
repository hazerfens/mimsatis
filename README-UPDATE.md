# Mim Satış — SQLite verisini koruyarak güncelleme

1. SQLite veritabanını ve uploads dizinini ayrıca yedekleyin. Uygulamayı güncelleme sırasında durdurun veya yazma trafiğini kesin.
2. Coolify kalıcı mount'lar: `/srv/mim-satis/data` -> `/app/data` ve `/srv/mim-satis/uploads` -> `/app/uploaded_files`. Var olan SQLite dosyasının `/srv/mim-satis/data/mim_reklam_satis_sistemi.db` konumunda olduğundan emin olun.
3. Coolify ortamında `SECRET_KEY` (önceki kurulumla **aynı** değer), `DATABASE_PATH=/app/data/mim_reklam_satis_sistemi.db`, `UPLOAD_FOLDER=/app/uploaded_files`, `REQUIRE_EXISTING_DB=1` ayarlarını koruyun. `ADMIN_INITIAL_PASSWORD` yalnızca yeni/verisiz kurulum için gerekir.
4. Git deposunda dosyaları güncelleyin ve deploy edin. Başlangıç komutu mevcut DB'yi arar, eksik altı sütun için SQLite backup alır ve `ALTER TABLE ADD COLUMN` uygular. DB yoksa boş DB açmadan çıkar.
5. Logs'ta `SQLite backup created` ve `Schema updated` mesajlarını kontrol edin. Eski kayıtların açıldığını, kullanıcı girişini, müşteri, teklif, randevu, Excel ve e-posta işlemlerini test edin.

Eklenen sütunlar: user.title, user.email_pass, user.email_signature, user.allowed_menus, customer.trade_registry_no, interaction.planned_datetime. Mevcut tablolardaki kayıtlar silinmez. SQLite üzerinde yeni sütunların eklenmesi dosyayı teknik olarak değiştirdiğinden 'DB değişmeden' ifadesi **veriler korunarak şema yükseltmesi** anlamındadır.

Önemli: yeni sürüm eskisindeki PWA manifest ve service worker endpoint'lerini içermiyor; PWA kullanımı sürdürülmek isteniyorsa ayrıca geri eklenmelidir. Personel SMTP şifreleri bu sürümde veritabanında düz metin olarak tutuluyor; ayrıca güvenlik iyileştirmesi gerekir.
