from aiogram.fsm.state import State, StatesGroup


class RuleWizardStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_trigger_type = State()
    waiting_for_trigger_value = State()
    waiting_for_template_id = State()
    waiting_for_priority = State()
    waiting_for_cooldown = State()
    waiting_for_delay = State()
    waiting_for_auto_delete = State()
    waiting_for_test_input = State()


class TemplateWizardStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_content = State()
    waiting_for_button_text = State()
    waiting_for_button_url = State()


class ExceptionWizardStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_chat_id = State()
    waiting_for_reason = State()


class CollectionWizardStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_description = State()
    waiting_for_rename = State()


class CampaignWizardStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_content = State()
    waiting_for_collection = State()
    waiting_for_button_text = State()
    waiting_for_button_url = State()
    waiting_for_replace_content = State()
    waiting_for_exclusions = State()
    waiting_for_schedule_type = State()
    waiting_for_schedule_time = State()
    waiting_for_confirmation = State()


class PersonalAuthStates(StatesGroup):
    waiting_for_phone = State()
    waiting_for_code = State()
    waiting_for_2fa_password = State()


class SettingsWizardStates(StatesGroup):
    waiting_for_work_hours = State()
    waiting_for_away_cooldown = State()
