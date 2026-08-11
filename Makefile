.PHONY: help install fixture run offline test quality report clean

help:
	@echo "make install   Install dependencies"
	@echo "make run       Full pipeline against the live source (needs internet)"
	@echo "make offline   Full pipeline against the bundled sample fixture"
	@echo "make test      Run the test suite"
	@echo "make quality   Re-run the data quality checks on the built warehouse"
	@echo "make report    Regenerate charts and docs/REPORT.md"
	@echo "make clean     Remove the warehouse, snapshots and generated charts"

install:
	pip install -r requirements.txt

fixture:
	python scripts/make_fixture.py

run:
	python -m pipeline.run all

offline: fixture
	python -m pipeline.run all --offline

test:
	python -m pytest -q

quality:
	python -m pipeline.run quality

report:
	python -m pipeline.run report

clean:
	rm -rf warehouse data/raw docs/charts/*.png docs/REPORT.md
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
