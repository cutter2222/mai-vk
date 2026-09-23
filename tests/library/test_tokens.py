import pytest

from presentation_designer.library.tokens import DesignCode


@pytest.mark.parametrize(
    ("background", "text", "muted", "expected"),
    [
        ("#FFFFFF", "#111111", "#DDDDDD", "#111111"),
        ("#111111", "#FFFFFF", "#333333", "#FFFFFF"),
        ("#FFFFFF", "#111111", "#666666", "#666666"),
    ],
)
def test_muted_text_has_readable_contrast(background, text, muted, expected):
    code = DesignCode.from_profile(
        {
            "design_tokens": {
                "colors": {
                    "palette": [
                        {"role": "background", "hex": background},
                        {"role": "text", "hex": text},
                        {"role": "neutral", "hex": muted},
                    ]
                }
            }
        }
    )
    assert code.muted_color == expected
