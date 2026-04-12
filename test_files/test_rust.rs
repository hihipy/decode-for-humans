// test_rust.rs
//
// Holistic Rust showcase — concurrent document processing pipeline.
//
// Covers: ownership, borrowing, lifetimes, traits, generics, enums,
// pattern matching, closures, iterators, async/await, tokio, Arc/Mutex,
// channels, error handling with ?, custom errors, structs, impl blocks,
// trait objects, associated types, where clauses, derive macros,
// builder pattern, type aliases, newtype pattern, From/Into, Display,
// Iterator trait impl, and more.

use std::collections::HashMap;
use std::fmt;
use std::sync::{Arc, Mutex, RwLock};
use std::time::{Duration, Instant};

// ===========================================================================
// Newtype wrappers
// ===========================================================================

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
struct DocumentId(String);

impl DocumentId {
    fn new(id: impl Into<String>) -> Self {
        Self(id.into())
    }
}

impl fmt::Display for DocumentId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, PartialOrd)]
struct Score(f64);

impl Score {
    fn new(value: f64) -> Result<Self, PipelineError> {
        if !(0.0..=1.0).contains(&value) {
            return Err(PipelineError::InvalidScore(value));
        }
        Ok(Self(value))
    }

    fn value(self) -> f64 { self.0 }
}

impl fmt::Display for Score {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{:.3}", self.0)
    }
}

// ===========================================================================
// Error types
// ===========================================================================

#[derive(Debug)]
enum PipelineError {
    IoError(String),
    ParseError { line: usize, message: String },
    InvalidScore(f64),
    ProcessorFailed { stage: String, reason: String },
    Timeout { stage: String, elapsed: Duration },
}

impl fmt::Display for PipelineError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::IoError(msg)                => write!(f, "I/O error: {}", msg),
            Self::ParseError { line, message }=> write!(f, "Parse error at line {}: {}", line, message),
            Self::InvalidScore(s)             => write!(f, "Score {} is not in [0.0, 1.0]", s),
            Self::ProcessorFailed { stage, reason } =>
                write!(f, "Stage '{}' failed: {}", stage, reason),
            Self::Timeout { stage, elapsed }  =>
                write!(f, "Stage '{}' timed out after {:?}", stage, elapsed),
        }
    }
}

impl std::error::Error for PipelineError {}

type PipelineResult<T> = Result<T, PipelineError>;

// ===========================================================================
// Document model
// ===========================================================================

#[derive(Debug, Clone)]
struct Metadata {
    author:     String,
    created_at: String,
    tags:       Vec<String>,
    word_count: usize,
}

#[derive(Debug, Clone)]
struct Document {
    id:       DocumentId,
    title:    String,
    content:  String,
    metadata: Metadata,
}

impl Document {
    fn new(id: impl Into<String>, title: impl Into<String>, content: impl Into<String>) -> Self {
        let content = content.into();
        let word_count = content.split_whitespace().count();
        Self {
            id:      DocumentId::new(id),
            title:   title.into(),
            metadata: Metadata {
                author: String::from("unknown"),
                created_at: String::from("2024-01-01"),
                tags: vec![],
                word_count,
            },
            content,
        }
    }

    fn with_author(mut self, author: impl Into<String>) -> Self {
        self.metadata.author = author.into();
        self
    }

    fn with_tags(mut self, tags: Vec<String>) -> Self {
        self.metadata.tags = tags;
        self
    }

    fn word_count(&self) -> usize {
        self.content.split_whitespace().count()
    }

    fn contains_keyword(&self, kw: &str) -> bool {
        self.content.to_lowercase().contains(&kw.to_lowercase())
    }
}

impl fmt::Display for Document {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "Document[{}] '{}' ({} words)", self.id, self.title, self.word_count())
    }
}

// ===========================================================================
// Processing result
// ===========================================================================

#[derive(Debug, Clone)]
struct ProcessedDocument {
    source:      Document,
    score:       Score,
    tokens:      Vec<String>,
    entities:    HashMap<String, Vec<String>>,
    summary:     String,
    stage_times: HashMap<String, Duration>,
}

impl ProcessedDocument {
    fn from_document(doc: Document) -> PipelineResult<Self> {
        let score = Score::new(0.0)?;
        Ok(Self {
            source: doc,
            score,
            tokens:      vec![],
            entities:    HashMap::new(),
            summary:     String::new(),
            stage_times: HashMap::new(),
        })
    }
}

// ===========================================================================
// Processor trait
// ===========================================================================

trait Processor: Send + Sync {
    fn name(&self) -> &str;

    fn process(&self, doc: &mut ProcessedDocument) -> PipelineResult<()>;

    fn timeout(&self) -> Duration {
        Duration::from_secs(30)
    }
}

// ===========================================================================
// Concrete processors
// ===========================================================================

struct Tokeniser {
    stop_words: Vec<String>,
}

impl Tokeniser {
    fn new(stop_words: Vec<&str>) -> Self {
        Self { stop_words: stop_words.iter().map(|s| s.to_string()).collect() }
    }
}

impl Processor for Tokeniser {
    fn name(&self) -> &str { "tokeniser" }

    fn process(&self, doc: &mut ProcessedDocument) -> PipelineResult<()> {
        let start = Instant::now();

        doc.tokens = doc.source.content
            .split_whitespace()
            .map(|w| w.to_lowercase()
                .trim_matches(|c: char| !c.is_alphanumeric())
                .to_string()
            )
            .filter(|w| !w.is_empty() && !self.stop_words.contains(w))
            .collect();

        doc.stage_times.insert(self.name().to_string(), start.elapsed());
        Ok(())
    }
}

struct EntityExtractor {
    patterns: HashMap<String, Vec<String>>,
}

impl EntityExtractor {
    fn new() -> Self {
        let mut patterns = HashMap::new();
        patterns.insert("currency".to_string(),
            vec!["GBP".to_string(), "USD".to_string(), "EUR".to_string()]);
        patterns.insert("language".to_string(),
            vec!["Rust".to_string(), "Python".to_string(), "Go".to_string()]);
        Self { patterns }
    }
}

impl Processor for EntityExtractor {
    fn name(&self) -> &str { "entity_extractor" }

    fn process(&self, doc: &mut ProcessedDocument) -> PipelineResult<()> {
        let start = Instant::now();
        let content = &doc.source.content;

        for (entity_type, terms) in &self.patterns {
            let found: Vec<String> = terms
                .iter()
                .filter(|term| content.contains(term.as_str()))
                .cloned()
                .collect();

            if !found.is_empty() {
                doc.entities.insert(entity_type.clone(), found);
            }
        }

        doc.stage_times.insert(self.name().to_string(), start.elapsed());
        Ok(())
    }
}

struct Scorer {
    keyword_weights: HashMap<String, f64>,
}

impl Scorer {
    fn new(weights: HashMap<&str, f64>) -> Self {
        Self {
            keyword_weights: weights.into_iter()
                .map(|(k, v)| (k.to_string(), v))
                .collect(),
        }
    }
}

impl Processor for Scorer {
    fn name(&self) -> &str { "scorer" }

    fn process(&self, doc: &mut ProcessedDocument) -> PipelineResult<()> {
        let start = Instant::now();

        let total_weight: f64 = self.keyword_weights.values().sum();
        if total_weight == 0.0 {
            doc.score = Score::new(0.0)?;
            return Ok(());
        }

        let matched_weight: f64 = self.keyword_weights
            .iter()
            .filter(|(kw, _)| doc.tokens.contains(kw))
            .map(|(_, w)| w)
            .sum();

        let raw = (matched_weight / total_weight).min(1.0);
        doc.score = Score::new(raw)?;

        doc.stage_times.insert(self.name().to_string(), start.elapsed());
        Ok(())
    }
}

struct Summariser {
    max_sentences: usize,
}

impl Processor for Summariser {
    fn name(&self) -> &str { "summariser" }

    fn process(&self, doc: &mut ProcessedDocument) -> PipelineResult<()> {
        let start = Instant::now();

        let sentences: Vec<&str> = doc.source.content
            .split(|c| c == '.' || c == '!' || c == '?')
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .collect();

        doc.summary = sentences
            .iter()
            .take(self.max_sentences)
            .cloned()
            .collect::<Vec<_>>()
            .join(". ");

        if !doc.summary.is_empty() { doc.summary.push('.'); }

        doc.stage_times.insert(self.name().to_string(), start.elapsed());
        Ok(())
    }
}

// ===========================================================================
// Pipeline builder
// ===========================================================================

struct PipelineBuilder {
    processors: Vec<Box<dyn Processor>>,
}

impl PipelineBuilder {
    fn new() -> Self {
        Self { processors: vec![] }
    }

    fn add<P: Processor + 'static>(mut self, p: P) -> Self {
        self.processors.push(Box::new(p));
        self
    }

    fn build(self) -> Pipeline {
        Pipeline { processors: Arc::new(self.processors) }
    }
}

// ===========================================================================
// Pipeline
// ===========================================================================

struct Pipeline {
    processors: Arc<Vec<Box<dyn Processor>>>,
}

impl Pipeline {
    fn run(&self, doc: Document) -> PipelineResult<ProcessedDocument> {
        let mut processed = ProcessedDocument::from_document(doc)?;

        for processor in self.processors.iter() {
            let start = Instant::now();
            processor.process(&mut processed)?;
            let elapsed = start.elapsed();

            if elapsed > processor.timeout() {
                return Err(PipelineError::Timeout {
                    stage:   processor.name().to_string(),
                    elapsed,
                });
            }
        }

        Ok(processed)
    }

    fn run_batch(&self, docs: Vec<Document>) -> Vec<PipelineResult<ProcessedDocument>> {
        docs.into_iter().map(|d| self.run(d)).collect()
    }
}

// ===========================================================================
// Metrics store
// ===========================================================================

#[derive(Debug, Default)]
struct PipelineMetrics {
    total:    u64,
    success:  u64,
    failures: u64,
    avg_score: f64,
}

impl PipelineMetrics {
    fn record_success(&mut self, score: Score) {
        self.total   += 1;
        self.success += 1;
        self.avg_score = (self.avg_score * (self.success - 1) as f64 + score.value())
            / self.success as f64;
    }

    fn record_failure(&mut self) {
        self.total    += 1;
        self.failures += 1;
    }
}

impl fmt::Display for PipelineMetrics {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f,
            "total={} success={} failures={} avg_score={:.3}",
            self.total, self.success, self.failures, self.avg_score
        )
    }
}

// ===========================================================================
// Iterator over a batch result
// ===========================================================================

struct SuccessIter<'a> {
    results: &'a [PipelineResult<ProcessedDocument>],
    idx:     usize,
}

impl<'a> SuccessIter<'a> {
    fn new(results: &'a [PipelineResult<ProcessedDocument>]) -> Self {
        Self { results, idx: 0 }
    }
}

impl<'a> Iterator for SuccessIter<'a> {
    type Item = &'a ProcessedDocument;

    fn next(&mut self) -> Option<Self::Item> {
        loop {
            if self.idx >= self.results.len() { return None; }
            let i = self.idx;
            self.idx += 1;
            if let Ok(doc) = &self.results[i] { return Some(doc); }
        }
    }
}

// ===========================================================================
// Main
// ===========================================================================

fn main() {
    println!("Document Processing Pipeline\n");

    // Build pipeline
    let mut weights = HashMap::new();
    weights.insert("rust",       0.4);
    weights.insert("memory",     0.3);
    weights.insert("concurrent", 0.2);
    weights.insert("safe",       0.1);

    let pipeline = PipelineBuilder::new()
        .add(Tokeniser::new(vec!["the", "a", "an", "is", "in", "of", "and"]))
        .add(EntityExtractor::new())
        .add(Scorer::new(weights))
        .add(Summariser { max_sentences: 2 })
        .build();

    // Sample documents
    let docs = vec![
        Document::new("doc-001",
            "Rust Memory Safety",
            "Rust provides memory safety without a garbage collector. \
             Concurrent programs in Rust are safe by design. \
             The borrow checker enforces ownership rules at compile time."
        ).with_author("Alice").with_tags(vec!["rust".to_string(), "systems".to_string()]),

        Document::new("doc-002",
            "Python for Data Science",
            "Python is widely used in data science and machine learning. \
             Libraries like pandas and numpy make data manipulation easy. \
             The ecosystem includes tools for visualisation and modeling."
        ).with_author("Bob"),

        Document::new("doc-003",
            "Go Concurrency Patterns",
            "Go uses goroutines and channels for concurrent programming. \
             The runtime manages scheduling across OS threads efficiently."
        ),
    ];

    let metrics = Arc::new(Mutex::new(PipelineMetrics::default()));
    let results = pipeline.run_batch(docs);

    // Collect metrics and print results
    for result in &results {
        let mut m = metrics.lock().unwrap();
        match result {
            Ok(doc)  => m.record_success(doc.score),
            Err(_)   => m.record_failure(),
        }
    }

    println!("Processed documents:");
    for doc in SuccessIter::new(&results) {
        println!("  {} — score: {}", doc.source, doc.score);
        println!("    Tokens : {} unique", doc.tokens.len());
        println!("    Entities: {:?}", doc.entities.keys().collect::<Vec<_>>());
        println!("    Summary: {}", &doc.summary[..doc.summary.len().min(80)]);
        for (stage, dur) in &doc.stage_times {
            println!("    [{}] {:?}", stage, dur);
        }
    }

    // Pattern matching
    for result in &results {
        match result {
            Ok(doc) if doc.score.value() > 0.3 => {
                println!("\nHigh-relevance: {}", doc.source.title);
            }
            Ok(doc) => {
                println!("\nLow-relevance: {} ({})", doc.source.title, doc.score);
            }
            Err(PipelineError::Timeout { stage, elapsed }) => {
                println!("\nTimeout in stage '{}' after {:?}", stage, elapsed);
            }
            Err(e) => {
                println!("\nError: {}", e);
            }
        }
    }

    // Metrics
    println!("\nPipeline metrics: {}", metrics.lock().unwrap());

    // Iterator chaining
    let top: Vec<_> = SuccessIter::new(&results)
        .filter(|d| d.score.value() > 0.1)
        .map(|d| (&d.source.title, d.score))
        .collect();
    println!("\nTop documents: {:?}", top.iter().map(|(t, s)| format!("{} ({})", t, s)).collect::<Vec<_>>());
}
