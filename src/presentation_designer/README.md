# presentation_designer

Ядро сервиса «Цифровой дизайнер презентаций».

Слои: parsing → generation → layout → export → audit; pipeline оркестрирует этапы; api и cli это точки входа; llm единственный путь к моделям; contracts и shared общие для всех слоёв.
