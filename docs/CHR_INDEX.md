# CHR hospital comparison

CHR index opens comparisons automatically using the selected center’s saved five-digit ZIP and MRI/CT prices. Missing center prices use zero as a comparison threshold, not a claim that the exam is free. A missing ZIP shows an unavailable message.

Configure CHR_DATABASE_URL on the Django server to a PostgreSQL database named selfpay_hospital_compare_v10. Keep credentials out of Git and frontend configuration. The database must contain the hospital tables, data and public.hospitals_near function from the v10 hospital database.

GET /api/chr-index?zip=45459&mri=450&ct=0 requires the existing guest/member session. For each modality it calls hospitals_near(zip, exam, price, 50). Only hospitals with a lowest cash price strictly above the center threshold are returned, nearest first within 50 miles. Results group by hospital CCN with separate MRI/CT tabs. Cash amounts are minimums; gross and insurer ranges remain their respective supplied values. These are grouped noncontrast exams, not exact procedure quotes. Distances are approximate ZIP-reference miles.

Connections enforce read-only transactions and an eight-second statement timeout. Missing configuration or database failures return a generic 503. Invalid ZIP/prices return 400. Twelve backend unit tests cover zero thresholds, minimum-price comparison, validation and grouping. Live local queries verify the radius and distance order.

Deploy backend code and set CHR_DATABASE_URL, then restart myselfpay-backend.service. Deploy the frontend separately on Vercel. Existing API routing must forward /api/chr-index to Django. No application migration creates or fills the separate hospital database; restore that database independently before enabling comparisons.
