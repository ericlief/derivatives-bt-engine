from setuptools import setup, find_packages

setup(
    name="options-bt",
    version="1.1",
    packages=find_packages(),
    install_requires=[
        line.strip()
        for line in open("requirements.txt")
        if line.strip() and not line.strip().startswith("#")
    ],
) 