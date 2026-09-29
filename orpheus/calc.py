"""Sums said aloud: "15 умножить на 37", "20% от 3 000", "корень из 144", "2 в степени 10".

What was heard is read into tokens (numbers - digits, "3 000", "2,5" - and operators in words or
signs) and computed by a small recursive-descent parser; anything else is not a sum (None), so
"сколько будет стоить ремонт" goes on to the model.
"""

import math
import re

from .numbers import to_digits

# longest first: "умножить на" before "на"
OPERATORS = [
    (r"квадратный\s+корень\s+из|корень\s+квадратный\s+из|корень\s+из|корень|√", "sqrt"),
    (r"в\s+квадрате", "square"),
    (r"в\s+кубе", "cube"),
    (r"в\s+степени|\^|\*\*", "pow"),
    (r"(?:процент(?:а|ов)?|%)\s+от", "percent_of"),
    (r"процент(?:а|ов)?|%", "percent"),
    (r"умножить\s+на|умножь\s+на|умноженное\s+на|помножить\s+на|помножь\s+на|умножаем\s+на|\*|×|·|[xх](?=\s*\d)|на", "*"),
    (r"разделить\s+на|раздели\s+на|делить\s+на|подели\s+на|поделить\s+на|деленное\s+на|делённое\s+на|поделенное\s+на|/|÷", "/"),
    (r"плюс|прибавить|\+", "+"),
    (r"минус|отнять|вычесть|-|−|–", "-"),
    (r"\(", "("),
    (r"\)", ")"),
]
NUMBER = r"\d{1,3}(?:[  ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
TOKEN = re.compile(r"\s*(?:(?P<num>%s)|%s)" % (NUMBER, "|".join("(?P<op%d>%s)" % (i, p) for i, (p, _) in enumerate(OPERATORS))),
                   re.I)
SPOKEN = {"+": "плюс", "-": "минус", "*": "умножить на", "/": "разделить на"}


class NotASum(Exception):
    pass


def tokenize(text):
    text = to_digits(text).strip().rstrip("?.!=").strip()
    text = re.sub(r"^(?:сколько|будет|это|получится|равно)\s+", "", text, flags=re.I)
    # "15% чаевых от 2400", "20 процентов скидки от 3000": the words between are what the percent is of
    text = re.sub(r"(%|процент(?:а|ов)?)\s+(?:[а-яё]+\s+){1,3}(?=от\s)", r"\1 ", text, flags=re.I)
    tokens, i = [], 0
    while i < len(text):
        m = TOKEN.match(text, i)
        if not m or m.end() == i:
            if text[i:].strip() == "":
                break
            raise NotASum(text[i:])
        if m.group("num"):
            tokens.append(("num", float(m.group("num").replace(" ", "").replace(" ", "").replace(",", "."))))
        else:
            op = next(OPERATORS[int(k[2:])][1] for k, v in m.groupdict().items() if k.startswith("op") and v)
            tokens.append(("op", op))
        i = m.end()
    return tokens


class _Parser:
    def __init__(self, tokens):
        self.t = tokens
        self.i = 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self, op=None):
        kind, value = self.peek()
        if op is not None and (kind != "op" or value != op):
            return False
        self.i += 1
        return True

    def expr(self):
        value = self.term()
        while self.peek() in (("op", "+"), ("op", "-")):
            op = self.t[self.i][1]
            self.i += 1
            rhs = self.term()
            if self.peek() == ("op", "percent"):  # "300 плюс 15%": 15% of 300
                self.i += 1
                rhs = value * rhs / 100
            value = value + rhs if op == "+" else value - rhs
        return value

    def term(self):
        value = self.power()
        while self.peek() in (("op", "*"), ("op", "/"), ("op", "percent_of")):
            op = self.t[self.i][1]
            self.i += 1
            rhs = self.power()
            if op == "*":
                value *= rhs
            elif op == "/":
                if rhs == 0:
                    raise ZeroDivisionError
                value /= rhs
            else:
                value = value * rhs / 100
        return value

    def power(self):
        value = self.unary()
        while True:
            if self.take("square"):
                value **= 2
            elif self.take("cube"):
                value **= 3
            elif self.take("pow"):
                value **= self.unary()
            else:
                return value

    def unary(self):
        if self.take("-"):
            return -self.unary()
        if self.take("sqrt"):
            v = self.unary()
            if v < 0:
                raise NotASum("корень из отрицательного")
            return math.sqrt(v)
        if self.take("("):
            v = self.expr()
            self.take(")")
            return v
        kind, value = self.peek()
        if kind != "num":
            raise NotASum("нет числа")
        self.i += 1
        return value


def evaluate(text):
    """-> (value, the sum in words and digits) or None when this is not a sum.
    Raises ZeroDivisionError for a division by zero."""
    try:
        tokens = tokenize(text)
    except NotASum:
        return None
    if not tokens or not any(k == "op" for k, _ in tokens) and len(tokens) != 1:
        return None
    if not any(k == "num" for k, _ in tokens) or all(k == "num" for k, _ in tokens):
        return None
    parser = _Parser(tokens)
    try:
        value = parser.expr()
    except NotASum:
        return None
    except OverflowError:
        return None
    if parser.i != len(tokens) or isinstance(value, complex) or math.isinf(value) or math.isnan(value):
        return None
    return value, spoken(tokens)


def spoken(tokens):
    words = []
    for kind, value in tokens:
        if kind == "num":
            words.append(number(value))
        else:
            words.append({"sqrt": "корень из", "square": "в квадрате", "cube": "в кубе", "pow": "в степени",
                          "percent_of": "% от", "percent": "%", "(": "(", ")": ")"}.get(value) or SPOKEN[value])
    return re.sub(r"\s+%", "%", " ".join(words)).replace("( ", "(").replace(" )", ")")


def number(value):
    """555.0 -> "555", 3.3333 -> "3,33", 1234567 -> "1 234 567"."""
    sign, value = ("-" if value < 0 else ""), abs(value)
    if abs(value - round(value)) < 1e-9:
        return sign + "{:,}".format(int(round(value))).replace(",", " ")
    text = ("%.2f" % value).rstrip("0").rstrip(".")
    whole, _, frac = text.partition(".")
    return sign + "{:,}".format(int(whole)).replace(",", " ") + ("," + frac if frac else "")
