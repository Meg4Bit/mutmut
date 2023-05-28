#!/bin/bash

export PYTHONPATH=$PYTHONPATH:$MUTMUT

thresholds=(80 60 40 20 0)

git stash && git checkout master
git checkout HEAD~100
echo "First revision"
time python -m mutmut run --paths-to-mutate=$1 --paths-to-exclude=$4 --use-coverage -n $3 --runner "$2"
mv .coverage .coverage_old

for t in ${thresholds[@]}; do
  git stash && git checkout master
  git checkout HEAD~$t
  echo "Modified mutmut"
  time python -m mutmut run --paths-to-mutate=$1 --paths-to-exclude=$4 --use-coverage -n $3 --runner "$2"
  mv .coverage .coverage_old
  mv .mutmut-cache .mutmut-cache_old
  echo "Original mutmut"
  git stash
  time python -m mutmut run --paths-to-mutate=$1 --paths-to-exclude=$4 --use-coverage -n $3 --runner "$2"
  rm -rf .mutmut-cache .coverage
  mv .mutmut-cache_old .mutmut-cache
done

git stash && git checkout master
