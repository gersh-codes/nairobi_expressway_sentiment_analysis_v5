import React, { useState } from 'react';
import './App.css';
import { Bar } from 'react-chartjs-2';
import { Chart as ChartJS, CategoryScale, LinearScale, BarElement, Title } from 'chart.js';

ChartJS.register(CategoryScale, LinearScale, BarElement, Title);

function App() {
  const [text, setText] = useState('');
  const [stakeholder, setStakeholder] = useState('local_citizen');
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);

  const analyzeSentiment = async () => {
    setLoading(true);
    const res = await fetch('http://localhost:5000/sentiment', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ texts: [text], stakeholder }),
    });
    const data = await res.json();
    setResult(data[0]);
    setLoading(false);
  };

  const sentimentChartData = result && {
    labels: ['VADER', 'TextBlob', 'BERT', 'Swahili Lexicon'],
    datasets: [
      {
        label: 'Sentiment Score',
        data: [
          result.vader?.compound || 0,
          result.textblob?.polarity || 0,
          result.bert?.label === 'POSITIVE' ? 1 : result.bert?.label === 'NEGATIVE' ? -1 : 0,
          result.swahili?.score || 0
        ],
        backgroundColor: '#4c9aff',
      }
    ]
  };

  const sentimentChartOptions = {
    responsive: true,
    scales: { y: { min: -1, max: 1 } }
  };

  return (
    <div className="container">
      <h1>Nairobi Expressway Sentiment Analyzer</h1>
      <textarea
        placeholder="Enter your comment here..."
        value={text}
        onChange={e => setText(e.target.value)}
      />
      <br />
      <select value={stakeholder} onChange={e => setStakeholder(e.target.value)}>
        <option value="local_citizen">Local Citizen</option>
        <option value="business_owner">Business Owner</option>
        <option value="government">Government Representative</option>
      </select>
      <button onClick={analyzeSentiment} disabled={loading}>
        {loading ? 'Analyzing...' : 'Analyze Sentiment'}
      </button>

      {result && (
        <div className="result-box">
          <h3>Results</h3>
          <p><strong>VADER:</strong> {result.vader.label} ({result.vader.compound})</p>
          <p><strong>TextBlob:</strong> {result.textblob.label} ({result.textblob.polarity})</p>
          <p><strong>BERT:</strong> {result.bert.label}</p>
          <p><strong>Swahili Lexicon:</strong> {result.swahili.label} ({result.swahili.score})</p>
        </div>
      )}

      {result && (
        <div className="chart-section">
          <h3>Sentiment Comparison</h3>
          <Bar data={sentimentChartData} options={sentimentChartOptions} />
        </div>
      )}
    </div>
  );
}

export default App;
