# Publish Guide

## 1) Create GitHub repo and push

```bash
cd /Users/pixelsama/Dev/Github/Skills/babeldoc-jobpack
git init
git add .
git commit -m "feat: initial babeldoc jobpack workflow"
gh repo create pixelsama/babeldoc-jobpack --public --source=. --remote=origin --push
```

## 2) Build artifacts

```bash
cd /Users/pixelsama/Dev/Github/Skills/babeldoc-jobpack
uv build
```

## 3) Publish to TestPyPI first

```bash
uv tool install twine
twine upload --repository testpypi dist/*
```

## 4) Publish to PyPI

```bash
twine upload dist/*
```

## 5) Install check

```bash
python3 -m venv /tmp/babeldoc-jobpack-check
/tmp/babeldoc-jobpack-check/bin/python -m pip install --upgrade pip
/tmp/babeldoc-jobpack-check/bin/pip install babeldoc-jobpack
/tmp/babeldoc-jobpack-check/bin/babeldoc-jobpack --help
```

