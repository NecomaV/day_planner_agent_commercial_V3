from __future__ import annotations


RECIPES = [
    {
        "name": "Овсянка с бананом и медом",
        "meal": "breakfast",
        "ingredients": ["oats", "milk", "banana", "honey"],
    },
    {
        "name": "Йогурт с ягодами и гранолой",
        "meal": "breakfast",
        "ingredients": ["yogurt", "berries", "granola", "honey"],
    },
    {
        "name": "Яйца и тосты",
        "meal": "breakfast",
        "ingredients": ["eggs", "butter", "bread"],
    },
    {
        "name": "Тост с авокадо",
        "meal": "breakfast",
        "ingredients": ["avocado", "bread", "olive oil", "lemon"],
    },
    {
        "name": "Творог с овощами",
        "meal": "breakfast",
        "ingredients": ["cottage cheese", "tomato", "cucumber", "olive oil"],
    },
    {
        "name": "Протеиновый смузи",
        "meal": "breakfast",
        "ingredients": ["milk", "banana", "protein powder", "peanut butter"],
    },
]


INGREDIENT_ALIASES = {
    "овсянка": "oats",
    "овсяные хлопья": "oats",
    "молоко": "milk",
    "банан": "banana",
    "мед": "honey",
    "мёд": "honey",
    "йогурт": "yogurt",
    "греческий йогурт": "yogurt",
    "ягоды": "berries",
    "ягода": "berries",
    "черника": "berries",
    "клубника": "berries",
    "малина": "berries",
    "гранола": "granola",
    "яйца": "eggs",
    "яйцо": "eggs",
    "сливочное масло": "butter",
    "масло": "butter",
    "хлеб": "bread",
    "тост": "bread",
    "авокадо": "avocado",
    "оливковое масло": "olive oil",
    "лимон": "lemon",
    "творог": "cottage cheese",
    "помидор": "tomato",
    "томат": "tomato",
    "огурец": "cucumber",
    "протеин": "protein powder",
    "протеиновый порошок": "protein powder",
    "протеиновая смесь": "protein powder",
    "арахисовая паста": "peanut butter",
}


INGREDIENT_DISPLAY = {
    "oats": "овсянка",
    "milk": "молоко",
    "banana": "банан",
    "honey": "мед",
    "yogurt": "йогурт",
    "berries": "ягоды",
    "granola": "гранола",
    "eggs": "яйца",
    "butter": "сливочное масло",
    "bread": "хлеб",
    "avocado": "авокадо",
    "olive oil": "оливковое масло",
    "lemon": "лимон",
    "cottage cheese": "творог",
    "tomato": "помидор",
    "cucumber": "огурец",
    "protein powder": "протеин",
    "peanut butter": "арахисовая паста",
}


def _norm(item: str) -> str:
    cleaned = item.strip().lower()
    return INGREDIENT_ALIASES.get(cleaned, cleaned)


def suggest_meals(pantry_items: list[str], meal: str = "breakfast", limit: int = 3) -> list[dict]:
    pantry = {_norm(i) for i in pantry_items if i.strip()}
    if not pantry:
        return []

    results = []
    for recipe in RECIPES:
        if recipe["meal"] != meal:
            continue
        ingredients = [_norm(i) for i in recipe["ingredients"]]
        missing = [i for i in ingredients if i not in pantry]
        score = len(ingredients) - len(missing)
        missing_display = [INGREDIENT_DISPLAY.get(i, i) for i in missing]
        results.append(
            {
                "name": recipe["name"],
                "ingredients": ingredients,
                "missing": missing_display,
                "score": score,
            }
        )

    results.sort(key=lambda r: (r["score"], -len(r["missing"])), reverse=True)
    return results[:limit]
