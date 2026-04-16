.PHONY: run test demo

run:
	python app/main.py

test:
	pytest -q

demo:
	python scripts/auto_demo_flow.py
