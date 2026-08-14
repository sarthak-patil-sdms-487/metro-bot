.PHONY: venv install

venv:
	python3 -m venv .venv

install:
	. .venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt -r requirements-dev.txt
