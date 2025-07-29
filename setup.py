from setuptools import setup, find_packages

setup(
    name="options-bt",
    version="0.1",
    packages=find_packages(),
    install_requires=[
        "pandas",
        "numpy",
        "matplotlib",
        "ipykernel",
        "pytest",
        "oauth2client==4.1.3",
        "gspread",
        "requests",
        "websockets",
    ],
) 