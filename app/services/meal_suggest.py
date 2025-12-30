from __future__ import annotations


RECIPES = [
    {
        "name": "Oatmeal with banana",
        "meal": "breakfast",
        "ingredients": ["oats", "milk", "banana", "honey"],
    },
    {
        "name": "Greek yogurt bowl",
        "meal": "breakfast",
        "ingredients": ["yogurt", "berries", "granola", "honey"],
    },
    {
        "name": "Scrambled eggs and toast",
        "meal": "breakfast",
        "ingredients": ["eggs", "butter", "bread"],
    },
    {
        "name": "Avocado toast",
        "meal": "breakfast",
        "ingredients": ["avocado", "bread", "olive oil", "lemon"],
    },
    {
        "name": "Cottage cheese plate",
        "meal": "breakfast",
        "ingredients": ["cottage cheese", "tomato", "cucumber", "olive oil"],
    },
    {
        "name": "Protein smoothie",
        "meal": "breakfast",
        "ingredients": ["milk", "banana", "protein powder", "peanut butter"],
    },
]


def _norm(item: str) -> str:
    return item.strip().lower()


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
        results.append(
            {
                "name": recipe["name"],
                "ingredients": ingredients,
                "missing": missing,
                "score": score,
            }
        )

    results.sort(key=lambda r: (r["score"], -len(r["missing"])), reverse=True)
    return results[:limit]
