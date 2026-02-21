DOCKER_IMAGE_BY_PREFIX = {
    # ---------- Python ----------
    "python": "python:3.12-slim",
    "python3": "python:3.12-slim",
    "pip": "python:3.12-slim",
    "pip3": "python:3.12-slim",
    "pytest": "python:3.12-slim",
    "ruff": "python:3.12-slim",
    "black": "python:3.12-slim",
    "mypy": "python:3.12-slim",
    # note: django-admin is provided by Django in the environment;
    # if you want it inside docker, you must ensure Django installed in image
    "django-admin": "python:3.12-slim",

    # ---------- Node / JavaScript ----------
    "node": "node:20",
    "npm": "node:20",
    "npx": "node:20",
    "yarn": "node:20",
    "pnpm": "node:20",
    "bun": "oven/bun:1",

    # ---------- Java / JVM ----------
    "java": "eclipse-temurin:21-jdk",
    "javac": "eclipse-temurin:21-jdk",
    "mvn": "maven:3.9-eclipse-temurin-21",
    "mvnw": "maven:3.9-eclipse-temurin-21",
    "gradle": "gradle:8-jdk21",
    "gradlew": "gradle:8-jdk21",

    # Kotlin (you allow these, but mapping is missing right now) :contentReference[oaicite:2]{index=2}
    # There isn’t an “official” single canonical Kotlin compiler image;
    # simplest is to run it in a JDK image and install Kotlin in the container (see note below).
    "kotlinc": "eclipse-temurin:21-jdk",
    "kotlin": "eclipse-temurin:21-jdk",

    # Scala / sbt
    "scala": "eclipse-temurin:21-jdk",
    "scalac": "eclipse-temurin:21-jdk",
    "sbt": "eclipse-temurin:21-jdk",

    # ---------- Go ----------
    "go": "golang:1.22",

    # ---------- Rust ----------
    "cargo": "rust:1",
    "rustc": "rust:1",

    # ---------- .NET ----------
    "dotnet": "mcr.microsoft.com/dotnet/sdk:8.0",

    # ---------- Ruby ----------
    "ruby": "ruby:3.2",
    "gem": "ruby:3.2",
    "bundle": "ruby:3.2",

    # ---------- PHP ----------
    "php": "php:8.2-cli",
    "composer": "composer:2",

    # ---------- Swift ----------
    "swift": "swift:5.9",
    "swiftc": "swift:5.9",

    # ---------- C / C++ toolchain ----------
    "gcc": "gcc:13",
    "g++": "gcc:13",
    "make": "gcc:13",
    "cmake": "kitware/cmake:latest",

    # ---------- “Other” languages you allow ----------
    "perl": "perl:5.38",
    "lua": "lua:5.4",
    "Rscript": "r-base:4.4",

    "ghc": "haskell:9",
    "cabal": "haskell:9",

    "elixir": "elixir:1.15",
    "mix": "elixir:1.15",

    # ---------- Git & basic shell ----------
    # alpine has git + coreutils availability; but note some commands differ from GNU versions
    "git": "alpine:3.19",
    "cd": "alpine:3.19",
    "ls": "alpine:3.19",
    "pwd": "alpine:3.19",
    "cat": "alpine:3.19",
    "head": "alpine:3.19",
    "tail": "alpine:3.19",
    "mkdir": "alpine:3.19",
    "echo": "alpine:3.19",
}