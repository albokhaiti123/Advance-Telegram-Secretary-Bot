import math
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from bot.keyboards.builders import (
    build_rule_details_keyboard,
    build_rules_list_keyboard,
    build_templates_list_keyboard,
    build_trigger_types_keyboard,
)
from bot.keyboards.main_menu import get_section_submenu_keyboard
from bot.keyboards.navigation import (
    build_back_and_home_row,
    build_cancel_keyboard,
    build_confirm_cancel_keyboard,
)
from bot.states.admin_states import RuleWizardStates
from database.database import DatabaseManager
from database.models import AutoReplyRule, ReplyTemplate
from security.access_control import AccessControl
from security.validators import InputValidator
from services.rule_engine import IncomingMessageEnvelope, RuleEngine

rules_router = Router(name="rules_router")
PAGE_SIZE = 8


@rules_router.callback_query(F.data.startswith("rules:list"))
async def cb_list_rules(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
) -> None:
    """عرض قائمة قواعد الرد التلقائي مرتبة حسب الأولوية."""
    parts = (callback.data or "rules:list:1").split(":")
    page = int(parts[-1]) if parts[-1].isdigit() else 1

    async with db_manager.session() as session:
        total = await session.scalar(select(func.count()).select_from(AutoReplyRule)) or 0
        total_pages = max(1, math.ceil(total / PAGE_SIZE))
        stmt = (
            select(AutoReplyRule)
            .order_by(AutoReplyRule.priority.desc(), AutoReplyRule.id.asc())
            .offset((page - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
        rules = list((await session.scalars(stmt)).all())

    text = (
        f"📋 <b>قائمة قواعد الردود التلقائية ({total})</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "اختر أي قاعدة لعرض تفاصيلها، تعديل أولويتها، أو معاينتها:"
    )
    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=build_rules_list_keyboard(rules, page, total_pages),
            parse_mode="HTML",
        )
    await callback.answer()


@rules_router.callback_query(F.data == "rules:priorities")
async def cb_rules_priorities_panel(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
) -> None:
    """شاشة تفاعلية سريعة لترتيب أولويات جميع القواعد في مكان واحد."""
    async with db_manager.session() as session:
        stmt = (
            select(AutoReplyRule)
            .order_by(AutoReplyRule.priority.desc(), AutoReplyRule.id.asc())
            .limit(15)
        )
        rules = list((await session.scalars(stmt)).all())

    rows = []
    lines = []
    for idx, r in enumerate(rules, start=1):
        badge = "🟢" if r.is_active else "🔴"
        lines.append(
            f"{idx}. {badge} <b>{r.name}</b> — الأولوية: <code>{r.priority}</code> ({r.trigger_type})"
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="⬆️ +10",
                    callback_data=f"rules:p_quick:up:{r.id}",
                ),
                InlineKeyboardButton(
                    text=f"[{r.priority}] {r.name[:18]}",
                    callback_data=f"rules:view:{r.id}",
                ),
                InlineKeyboardButton(
                    text="⬇️ -10",
                    callback_data=f"rules:p_quick:down:{r.id}",
                ),
            ]
        )

    rows.append(build_back_and_home_row("sec:rules"))
    body = "\n".join(lines) if lines else "لا توجد قواعد مضافة بعد."
    text = (
        "🔢 <b>ترتيب أولويات القواعد</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "يتم فحص القواعد من الأعلى أولوية إلى الأقل. استخدم أزرار (⬆️ +10) و(⬇️ -10) لإعادة الترتيب الفوري:\n\n"
        f"{body}"
    )

    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML",
        )
    await callback.answer()


@rules_router.callback_query(F.data.startswith("rules:p_quick:"))
async def cb_quick_adjust_priority(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
    access_control: AccessControl,
) -> None:
    """تعديل سريع لأولوية القاعدة من داخل شاشة ترتيب الأولويات."""
    parts = (callback.data or "").split(":")
    direction, rule_id = parts[2], int(parts[3])
    delta = 10 if direction == "up" else -10

    async with db_manager.session() as session:
        rule = await session.get(AutoReplyRule, rule_id)
        if not rule:
            await callback.answer("القاعدة غير موجودة", show_alert=True)
            return
        rule.priority = max(1, (rule.priority or 100) + delta)
        new_prio = rule.priority

    await access_control.log_admin_action(
        "change_rule_priority",
        target_id=str(rule_id),
        details={"new_priority": new_prio},
    )
    await callback.answer(f"تم تحديث الأولوية إلى {new_prio} ✅")
    await cb_rules_priorities_panel(callback, db_manager)


@rules_router.callback_query(F.data.startswith("rules:view:"))
async def cb_view_rule(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
) -> None:
    rule_id = int((callback.data or "").split(":")[-1])
    async with db_manager.session() as session:
        stmt = (
            select(AutoReplyRule)
            .options(selectinload(AutoReplyRule.template))
            .where(AutoReplyRule.id == rule_id)
        )
        rule = await session.scalar(stmt)

    if not rule:
        await callback.answer("القاعدة غير موجودة.", show_alert=True)
        return

    last_exec = (
        rule.last_executed_at.strftime("%Y-%m-%d %H:%M")
        if rule.last_executed_at
        else "لم تُنفذ بعد"
    )
    tpl_name = rule.template.name if rule.template else f"#{rule.template_id}"
    text = (
        f"⚙️ <b>تفاصيل القاعدة: {rule.name}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>الحالة:</b> {'🟢 مفعلة' if rule.is_active else '🔴 معطلة'}\n"
        f"• <b>نوع المحفز:</b> <code>{rule.trigger_type}</code>\n"
        f"• <b>قيمة المحفز:</b> <code>{rule.trigger_value or '—'}</code>\n"
        f"• <b>القالب المرتبط:</b> {tpl_name}\n"
        f"• <b>الأولوية:</b> {rule.priority}\n"
        f"• <b>فترة الانتظار (Cooldown):</b> {rule.cooldown_seconds} ثانية\n"
        f"• <b>التأخير قبل الرد:</b> {rule.delay_seconds} ثانية\n"
        f"• <b>عدد مرات التنفيذ:</b> {rule.execution_count}\n"
        f"• <b>آخر وقت تنفيذ:</b> {last_exec}"
    )
    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=build_rule_details_keyboard(rule),
            parse_mode="HTML",
        )
    await callback.answer()


@rules_router.callback_query(F.data.startswith("rules:toggle:"))
async def cb_toggle_rule(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
    access_control: AccessControl,
) -> None:
    rule_id = int((callback.data or "").split(":")[-1])
    async with db_manager.session() as session:
        rule = await session.get(AutoReplyRule, rule_id)
        if not rule:
            await callback.answer("القاعدة غير موجودة", show_alert=True)
            return
        rule.is_active = not rule.is_active
        new_state = rule.is_active

    await access_control.log_admin_action(
        "toggle_rule", target_id=str(rule_id), details={"is_active": new_state}
    )
    await cb_view_rule(callback, db_manager)


@rules_router.callback_query(F.data.startswith("rules:prio_"))
async def cb_change_rule_priority(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
) -> None:
    parts = (callback.data or "").split(":")
    action, rule_id = parts[1], int(parts[2])
    delta = 10 if action == "prio_up" else -10

    async with db_manager.session() as session:
        rule = await session.get(AutoReplyRule, rule_id)
        if rule:
            rule.priority = max(1, (rule.priority or 100) + delta)

    await cb_view_rule(callback, db_manager)


@rules_router.callback_query(F.data.startswith("rules:clone:"))
async def cb_clone_rule(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
    access_control: AccessControl,
) -> None:
    rule_id = int((callback.data or "").split(":")[-1])
    async with db_manager.session() as session:
        orig = await session.get(AutoReplyRule, rule_id)
        if not orig:
            await callback.answer("القاعدة غير موجودة", show_alert=True)
            return
        cloned = AutoReplyRule(
            name=f"{orig.name} ({orig.id}-نسخة)",
            trigger_type=orig.trigger_type,
            trigger_value=orig.trigger_value,
            template_id=orig.template_id,
            is_active=False,
            priority=orig.priority,
            allow_multiple=orig.allow_multiple,
            scope=orig.scope,
            target_chat_types=orig.target_chat_types,
            delay_seconds=orig.delay_seconds,
            cooldown_seconds=orig.cooldown_seconds,
            auto_delete_seconds=orig.auto_delete_seconds,
            conditions_json=dict(orig.conditions_json or {}),
        )
        session.add(cloned)
        await session.flush()
        new_id = cloned.id

    await access_control.log_admin_action("clone_rule", target_id=str(new_id))
    await callback.answer("تم نسخ القاعدة بنجاح (معطلة افتراضيًا للمراجعة).", show_alert=True)
    await cb_list_rules(callback, db_manager)


@rules_router.callback_query(F.data.startswith("rules:del_ask:"))
async def cb_ask_delete_rule(callback: CallbackQuery) -> None:
    rule_id = int((callback.data or "").split(":")[-1])
    if callback.message:
        await callback.message.edit_text(
            text="⚠️ <b>هل أنت متأكد من حذف هذه القاعدة نهائيًا؟</b>",
            reply_markup=build_confirm_cancel_keyboard(
                confirm_callback=f"rules:del_confirm:{rule_id}",
                cancel_callback=f"rules:view:{rule_id}",
            ),
            parse_mode="HTML",
        )
    await callback.answer()


@rules_router.callback_query(F.data.startswith("rules:del_confirm:"))
async def cb_confirm_delete_rule(
    callback: CallbackQuery,
    db_manager: DatabaseManager,
    access_control: AccessControl,
) -> None:
    rule_id = int((callback.data or "").split(":")[-1])
    async with db_manager.session() as session:
        rule = await session.get(AutoReplyRule, rule_id)
        if rule:
            await session.delete(rule)

    await access_control.log_admin_action("delete_rule", target_id=str(rule_id))
    await callback.answer("تم حذف القاعدة.", show_alert=True)
    await cb_list_rules(callback, db_manager)


@rules_router.callback_query(F.data == "rules:add")
async def cb_add_rule_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(RuleWizardStates.waiting_for_name)
    if callback.message:
        await callback.message.edit_text(
            text="➕ <b>إضافة قاعدة رد تلقائي جديدة</b>\nأرسل الآن اسمًا وصفيًا للقاعدة (مثال: الرد على الاستفسار عن الأسعار):",
            reply_markup=build_cancel_keyboard("sec:rules"),
            parse_mode="HTML",
        )
    await callback.answer()


@rules_router.message(RuleWizardStates.waiting_for_name)
async def msg_rule_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("⚠️ يرجى إدخال اسم واضح لا يقل عن حرفين.")
        return
    await state.update_data(name=name)
    await state.set_state(RuleWizardStates.waiting_for_trigger_type)
    await message.answer(
        text="اختر نوع المحفز (Trigger) لهذه القاعدة:",
        reply_markup=build_trigger_types_keyboard(),
    )


@rules_router.callback_query(
    RuleWizardStates.waiting_for_trigger_type,
    F.data.startswith("rules:set_trig:"),
)
async def cb_rule_trigger_type(
    callback: CallbackQuery,
    state: FSMContext,
    db_manager: DatabaseManager,
) -> None:
    trig_type = (callback.data or "").split(":")[-1]
    await state.update_data(trigger_type=trig_type)

    if trig_type in {"first_message", "any"}:
        await state.update_data(trigger_value=None)
        await _prompt_template_selection(callback, state, db_manager)
    else:
        await state.set_state(RuleWizardStates.waiting_for_trigger_value)
        prompt = (
            "أرسل الكلمات المفتاحية مفصولة بفاصلة (مثال: السعر، كم السعر، قائمة الأسعار):"
            if trig_type == "keyword"
            else "أرسل التعبير النمطي (Regex) للمطابقة:"
        )
        if callback.message:
            await callback.message.edit_text(
                text=prompt,
                reply_markup=build_cancel_keyboard("sec:rules"),
            )
    await callback.answer()


@rules_router.message(RuleWizardStates.waiting_for_trigger_value)
async def msg_rule_trigger_value(
    message: Message,
    state: FSMContext,
    db_manager: DatabaseManager,
) -> None:
    val = (message.text or "").strip()
    data = await state.get_data()
    if data.get("trigger_type") == "regex":
        ok, err = InputValidator.validate_regex(val)
        if not ok:
            await message.answer(f"⚠️ {err}\nيرجى إرسال تعبير نمطي صالح:")
            return

    await state.update_data(trigger_value=val)
    async with db_manager.session() as session:
        templates = list((await session.scalars(select(ReplyTemplate))).all())

    if not templates:
        await state.clear()
        await message.answer(
            "⚠️ لا توجد قوالب محفوظة بعد. يرجى إنشاء قالب أولًا من قسم «القوالب والوسائط».",
            reply_markup=get_section_submenu_keyboard("templates"),
        )
        return

    await state.set_state(RuleWizardStates.waiting_for_template_id)
    await message.answer(
        text="اختر القالب الذي سيتم إرساله عند تحقق هذه القاعدة:",
        reply_markup=build_templates_list_keyboard(templates, select_for_rule=True),
    )


async def _prompt_template_selection(
    callback: CallbackQuery,
    state: FSMContext,
    db_manager: DatabaseManager,
) -> None:
    async with db_manager.session() as session:
        templates = list((await session.scalars(select(ReplyTemplate))).all())
    if not templates:
        await state.clear()
        if callback.message:
            await callback.message.edit_text(
                "⚠️ لا توجد قوالب محفوظة بعد. أنشئ قالبًا أولًا من قسم «القوالب والوسائط».",
                reply_markup=get_section_submenu_keyboard("templates"),
            )
        return
    await state.set_state(RuleWizardStates.waiting_for_template_id)
    if callback.message:
        await callback.message.edit_text(
            text="اختر القالب الذي سيتم إرساله عند تحقق هذه القاعدة:",
            reply_markup=build_templates_list_keyboard(templates, select_for_rule=True),
        )


@rules_router.callback_query(
    RuleWizardStates.waiting_for_template_id,
    F.data.startswith("rules:pick_tpl:"),
)
async def cb_rule_pick_template(
    callback: CallbackQuery,
    state: FSMContext,
    db_manager: DatabaseManager,
    access_control: AccessControl,
) -> None:
    template_id = int((callback.data or "").split(":")[-1])
    data = await state.get_data()

    async with db_manager.session() as session:
        new_rule = AutoReplyRule(
            name=data["name"],
            trigger_type=data["trigger_type"],
            trigger_value=data.get("trigger_value"),
            template_id=template_id,
            is_active=True,
            priority=100,
            cooldown_seconds=60,
        )
        session.add(new_rule)
        await session.flush()
        rule_id = new_rule.id

    await state.clear()
    await access_control.log_admin_action("create_rule", target_id=str(rule_id))
    await callback.answer("تم حفظ القاعدة وتفعيلها بنجاح ✅", show_alert=True)
    await cb_list_rules(callback, db_manager)


@rules_router.callback_query(F.data == "rules:test")
async def cb_test_rules_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(RuleWizardStates.waiting_for_test_input)
    if callback.message:
        await callback.message.edit_text(
            text="🧪 <b>اختبار محرك القواعد</b>\nأرسل الآن أي نص رسالة افتراضية لمعرفة القواعد التي ستتطابق معها وأولوياتها:",
            reply_markup=build_cancel_keyboard("sec:rules"),
            parse_mode="HTML",
        )
    await callback.answer()


@rules_router.message(RuleWizardStates.waiting_for_test_input)
async def msg_test_rules_run(
    message: Message,
    state: FSMContext,
    db_manager: DatabaseManager,
) -> None:
    await state.clear()
    engine = RuleEngine(db_manager=db_manager)
    test_env = IncomingMessageEnvelope(
        chat_id=999999999,
        sender_id=888888888,
        chat_type="private",
        text=message.text or "",
        first_name="مستخدم_تجريبي",
    )
    matched = await engine.evaluate_message(test_env)
    if not matched:
        res_text = "ℹ️ لم تتطابق الرسالة مع أي قاعدة مفعلة حاليًا."
    else:
        lines = [
            f"{idx + 1}. <b>{r.name}</b> (المعرف: #{r.id} | المحفز: {r.trigger_type} | الأولوية: {r.priority})"
            for idx, r in enumerate(matched)
        ]
        res_text = "✅ <b>القواعد المطابقة بالترتيب:</b>\n\n" + "\n".join(lines)

    await message.answer(
        text=res_text,
        reply_markup=get_section_submenu_keyboard("rules"),
        parse_mode="HTML",
    )
