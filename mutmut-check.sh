export PYTHONPATH=$2:/home/pe4enko/Repos/mutmut

cd $1


thresholds=(50 100 200 300 400 500 600 700 800 900 1000 1100 1200 1300 1400)

for t in ${thresholds[@]}; do
  echo "Threshold is $t"
  rm -rf .mutmut-cache
  python -m mutmut run --paths-to-mutate=$2 --use-coverage -n $t | tail -n 1
done
