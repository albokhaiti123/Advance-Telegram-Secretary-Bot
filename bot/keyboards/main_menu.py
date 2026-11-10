from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from bot.keyboards.navigation import build_back_and_home_row


def get_main_dashboard_keyboard(auto_reply_enabled: bool = True) -> InlineKeyboardMarkup:
    """
    لوحة التحكم الرئيسية التفاعلية داخل تيليجرام.
    تضم الأقسام الـ 12 المحددة في وثيقة المواصفات بالترتيب.
    """
    status_icon = "🟢 مفعل" if auto_reply_enabled else "🔴 معطل"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"1️⃣ الردود التلقائية ({status_icon})",
                    callback_data="sec:auto_reply",
                ),
                InlineKeyboardButton(
                    text="2️⃣ إدارة القواعد",
                    callback_data="sec:rules",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="3️⃣ القوالب والوسائط",
                    callback_data="sec:templates",
                ),
                InlineKeyboardButton(
                    text="4️⃣ إدارة المحادثات",
                    callback_data="sec:conversations",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="5️⃣ إدارة الاستثناءات",
                    callback_data="sec:exceptions",
                ),
                InlineKeyboardButton(
                    text="6️⃣ بث المجموعات",
                    callback_data="sec:broadcasts",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="7️⃣ إدارة الحملات",
                    callback_data="sec:campaigns",
                ),
                InlineKeyboardButton(
                    text="8️⃣ المهام المجدولة",
                    callback_data="sec:scheduler",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="9️⃣ إحصائيات النظام",
                    callback_data="sec:statistics",
                ),
                InlineKeyboardButton(
                    text="🔟 سجل النشاط والأخطاء",
                    callback_data="sec:logs",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="1️⃣1️⃣ إعدادات الحساب والتكاملات",
                    callback_data="sec:account",
                ),
                InlineKeyboardButton(
                    text="1️⃣2️⃣ إعدادات النظام",
                    callback_data="sec:settings",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔄 تحديث لوحة التحكم",
                    callback_data="nav:refresh_main",
                )
            ],
        ]
    )


def get_section_submenu_keyboard(section_key: str, extra_state: bool = True) -> InlineKeyboardMarkup:
    """بناء القوائم الفرعية لكل قسم من الأقسام الـ 12 مع أزرار التنقل والرجوع."""
    menus = {
        "auto_reply": [
            [
                InlineKeyboardButton(
                    text="⏯️ تبديل حالة الرد التلقائي العام",
                    callback_data="ar:toggle_master",
                )
            ],
            [
                InlineKeyboardButton(text="🕒 إعدادات ساعات العمل", callback_data="ar:work_hours"),
                InlineKeyboardButton(text="🌙 وضع الغياب (Away)", callback_data="ar:away_mode"),
            ],
            [
                InlineKeyboardButton(text="👋 رسالة الترحيب", callback_data="ar:greeting"),
                InlineKeyboardButton(text="⚡ الردود السريعة", callback_data="ar:quick_replies"),
            ],
        ],
        "rules": [
            [
                InlineKeyboardButton(text="➕ إضافة قاعدة جديدة", callback_data="rules:add"),
                InlineKeyboardButton(text="📋 عرض القواعد الحالية", callback_data="rules:list:1"),
            ],
            [
                InlineKeyboardButton(text="🧪 اختبار مطابقة رسالة", callback_data="rules:test"),
                InlineKeyboardButton(text="🔢 ترتيب الأولويات", callback_data="rules:priorities"),
            ],
        ],
        "templates": [
            [
                InlineKeyboardButton(text="➕ إنشاء قالب جديد", callback_data="tpl:add"),
                InlineKeyboardButton(text="📂 قائمة القوالب", callback_data="tpl:list:1"),
            ],
            [
                InlineKeyboardButton(text="🖼️ مكتبة الوسائط المحفوظة", callback_data="tpl:media:1"),
                InlineKeyboardButton(text="🔘 إدارة الأزرار التفاعلية", callback_data="tpl:buttons"),
            ],
        ],
        "conversations": [
            [
                InlineKeyboardButton(text="💬 المحادثات النشطة", callback_data="conv:list:1"),
                InlineKeyboardButton(text="🔄 الردود التلقائية الأخيرة", callback_data="conv:managed:1"),
            ],
            [
                InlineKeyboardButton(text="🔓 فك تعليق العمليات", callback_data="conv:clear_locks"),
            ],
        ],
        "exceptions": [
            [
                InlineKeyboardButton(text="🚫 قائمة حظر المستخدمين", callback_data="exc:users_black:1"),
                InlineKeyboardButton(text="✅ قائمة سماح المستخدمين", callback_data="exc:users_white:1"),
            ],
            [
                InlineKeyboardButton(text="🔇 المحادثات المستثناة", callback_data="exc:chats_black:1"),
                InlineKeyboardButton(text="➕ إضافة استثناء جديد", callback_data="exc:add"),
            ],
        ],
        "broadcasts": [
            [
                InlineKeyboardButton(text="🔍 اكتشاف وتحديث المجموعات", callback_data="bc:discover"),
                InlineKeyboardButton(text="📋 قائمة المجموعات وصلاحياتها", callback_data="bc:groups:1"),
            ],
            [
                InlineKeyboardButton(text="🗂️ تصنيفات المجموعات (Collections)", callback_data="bc:collections:1"),
                InlineKeyboardButton(text="➕ إنشاء تصنيف جديد", callback_data="bc:col_add"),
            ],
            [
                InlineKeyboardButton(text="📢 إعداد رسالة بث جديدة", callback_data="camp:create"),
            ],
        ],
        "campaigns": [
            [
                InlineKeyboardButton(text="➕ إنشاء حملة جديدة", callback_data="camp:create"),
                InlineKeyboardButton(text="📊 الحملات الجارية والمجدولة", callback_data="camp:active:1"),
            ],
            [
                InlineKeyboardButton(text="📜 أرشيف الحملات ونتائجها", callback_data="camp:history:1"),
            ],
        ],
        "scheduler": [
            [
                InlineKeyboardButton(text="⏳ المهام المجدولة القادمة", callback_data="sch:pending:1"),
                InlineKeyboardButton(text="🔁 المهام الدورية المتكررة", callback_data="sch:recurring:1"),
            ],
            [
                InlineKeyboardButton(text="🧹 تنظيف المهام المعلقة/الفاشلة", callback_data="sch:cleanup"),
            ],
        ],
        "statistics": [
            [
                InlineKeyboardButton(text="📅 تقرير اليوم", callback_data="stat:period:daily"),
                InlineKeyboardButton(text="📆 تقرير الأسبوع", callback_data="stat:period:weekly"),
                InlineKeyboardButton(text="🗓️ تقرير الشهر", callback_data="stat:period:monthly"),
            ],
            [
                InlineKeyboardButton(text="🤖 إحصائيات الردود والقواعد", callback_data="stat:replies"),
                InlineKeyboardButton(text="📢 إحصائيات المجموعات والحملات", callback_data="stat:campaigns"),
            ],
        ],
        "logs": [
            [
                InlineKeyboardButton(text="⚠️ آخر الأخطاء والاستثناءات", callback_data="logs:errors:1"),
                InlineKeyboardButton(text="📝 سجل عمليات الإدارة", callback_data="logs:admin:1"),
            ],
            [
                InlineKeyboardButton(text="🗑️ سجل حذف واستبدال الردود", callback_data="logs:deletions:1"),
                InlineKeyboardButton(text="🔌 سجل الاتصالات", callback_data="logs:connections:1"),
            ],
        ],
        "account": [
            [
                InlineKeyboardButton(text="💼 حالة اتصال Telegram Business", callback_data="acc:business_status"),
                InlineKeyboardButton(text="🔍 فحص صلاحيات Business", callback_data="acc:business_check"),
            ],
            [
                InlineKeyboardButton(text="👤 حالة جلسة الحساب الشخصي", callback_data="acc:personal_status"),
                InlineKeyboardButton(text="🔐 ربط / إعادة تفويض الحساب الشخصي", callback_data="acc:personal_login"),
            ],
            [
                InlineKeyboardButton(text="🚪 فصل جلسة الحساب الشخصي بأمان", callback_data="acc:personal_logout_ask"),
            ],
        ],
        "settings": [
            [
                InlineKeyboardButton(text="🔔 إعدادات الإشعارات والتنبيهات", callback_data="sys:notifications"),
                InlineKeyboardButton(text="🌍 المنطقة الزمنية", callback_data="sys:timezone"),
            ],
            [
                InlineKeyboardButton(text="💾 إنشاء نسخة احتياطية فورية", callback_data="sys:backup_now"),
                InlineKeyboardButton(text="🩺 فحص سلامة قاعدة البيانات", callback_data="sys:health_check"),
            ],
        ],
    }

    rows = list(menus.get(section_key, []))
    rows.append(build_back_and_home_row("nav:main"))
    return InlineKeyboardMarkup(inline_keyboard=rows)
