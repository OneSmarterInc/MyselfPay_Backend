"""Approved chat questions and their deterministic directory searches."""
QUESTIONS = {
    'Knee MRI near Cincinnati under $500': {
        'query': 'Knee MRI near Cincinnati, OH under $500',
        'search': {'mod': 'MRI', 'body': 'Knee', 'contrast': 'unknown', 'location': 'Cincinnati, OH', 'budget': 500},
    },
    'Brain MRI without contrast near Hilo': {
        'query': 'Brain MRI without contrast near Hilo, HI',
        'search': {'mod': 'MRI', 'body': 'Brain', 'contrast': 'without', 'location': 'Hilo, HI', 'budget': 0},
    },
    'Knee MRI near Dayton': {
        'query': 'Knee MRI near Dayton, OH',
        'search': {'mod': 'MRI', 'body': 'Knee', 'contrast': 'unknown', 'location': 'Dayton, OH', 'budget': 0},
    },
}


def canonical_question(message):
    normalized = ' '.join(message.split()).casefold()
    return next((label for label in QUESTIONS if label.casefold() == normalized), None)


def preset_search(label):
    return {'scope': 'state', 'radius': 50, 'type': 'all', 'sort': 'distance', 'selected': [], **QUESTIONS[label]['search']}


def answer(result, label):
    search = result['search']
    total = sum(len(result.get(key, [])) for key in ('offers', 'programs', 'nearby_unpriced', 'program_entries'))
    description = f"{search['body']} {search['mod']} near {search['location']}"
    if search['contrast'] == 'without':
        description = f"{search['body']} {search['mod']} without contrast near {search['location']}"
    if search['budget']:
        description += f" with published prices up to ${search['budget']:g}"
    reply = (f"I found {total} matching listing{'s' if total != 1 else ''} for {description}. Review the centers below and confirm your exact exam, eligibility and total with the facility." if total else
        f"No matching listings are currently indexed for {description}. Try another predefined question or use Search & filters to adjust your search.")
    if search['budget']:
        reply += ' Centers without a published numeric price are excluded; a published reference is not a current quote.'
    result.update(reply=reply, predefined_question=label, agent_mode='predefined-questions')
    return result
